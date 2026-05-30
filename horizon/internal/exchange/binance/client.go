package binance

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"horizon/internal/exchange"
)

type BinanceSpotClient struct {
	apiKey     string
	secret     string
	recvWindow int
	baseURL    string
	httpClient *http.Client
}

type AdapterConfig struct {
	APIKey     string
	Secret     string
	RecvWindow int
}

func NewClient(cfg AdapterConfig) *BinanceSpotClient {
	return &BinanceSpotClient{
		apiKey:     cfg.APIKey,
		secret:     cfg.Secret,
		recvWindow: cfg.RecvWindow,
		baseURL:    "https://api.binance.com",
		httpClient: &http.Client{Timeout: 10 * time.Second},
	}
}

func (c *BinanceSpotClient) Name() string   { return "binance" }
func (c *BinanceSpotClient) IsEnabled() bool { return c.apiKey != "" }

type binanceTickerResp struct {
	Symbol      string `json:"symbol"`
	LastPrice   string `json:"lastPrice"`
	Volume      string `json:"volume"`
	QuoteVolume string `json:"quoteVolume"`
}

type binanceBalanceResp struct {
	Asset  string `json:"asset"`
	Free   string `json:"free"`
	Locked string `json:"locked"`
}

type binanceOrderResp struct {
	OrderID     int64  `json:"orderId"`
	Symbol      string `json:"symbol"`
	Side        string `json:"side"`
	Type        string `json:"type"`
	Price       string `json:"price"`
	OrigQty     string `json:"origQty"`
	ExecutedQty string `json:"executedQty"`
	Status      string `json:"status"`
	CreateTime  int64  `json:"time"`
	UpdateTime  int64  `json:"updateTime"`
}

func (c *BinanceSpotClient) FetchTicker(ctx context.Context, symbol string) (*exchange.Ticker, error) {
	ep := fmt.Sprintf("%s/api/v3/ticker/24hr?symbol=%s", c.baseURL, symbol)
	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-MBX-APIKEY", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ticker binanceTickerResp
	if err := json.NewDecoder(resp.Body).Decode(&ticker); err != nil {
		return nil, err
	}

	price, _ := strconv.ParseFloat(ticker.LastPrice, 64)
	vol, _ := strconv.ParseFloat(ticker.QuoteVolume, 64)

	return &exchange.Ticker{
		Symbol:    symbol,
		Price:     price,
		Volume24h: vol,
		Exchange:  "binance",
		UpdatedAt: time.Now().Unix(),
	}, nil
}

func (c *BinanceSpotClient) FetchOrderBook(ctx context.Context, symbol string, depth int) (*exchange.OrderBook, error) {
	ep := fmt.Sprintf("%s/api/v3/depth?symbol=%s&limit=%d", c.baseURL, symbol, depth)
	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ob struct {
		Bids [][]string `json:"bids"`
		Asks [][]string `json:"asks"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ob); err != nil {
		return nil, err
	}

	var book exchange.OrderBook
	book.Symbol = symbol
	book.Exchange = "binance"
	for _, b := range ob.Bids {
		price, _ := strconv.ParseFloat(b[0], 64)
		size, _ := strconv.ParseFloat(b[1], 64)
		book.Bids = append(book.Bids, exchange.OrderBookEntry{Price: price, Size: size})
	}
	for _, a := range ob.Asks {
		price, _ := strconv.ParseFloat(a[0], 64)
		size, _ := strconv.ParseFloat(a[1], 64)
		book.Asks = append(book.Asks, exchange.OrderBookEntry{Price: price, Size: size})
	}
	return &book, nil
}

func (c *BinanceSpotClient) FetchTrades(ctx context.Context, symbol string, limit int) ([]exchange.Trade, error) {
	ep := fmt.Sprintf("%s/api/v3/trades?symbol=%s&limit=%d", c.baseURL, symbol, limit)
	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var trades []struct {
		ID          int64  `json:"id"`
		Price       string `json:"price"`
		Qty         string `json:"qty"`
		Time        int64  `json:"time"`
		IsBuyerMaker bool  `json:"isBuyerMaker"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&trades); err != nil {
		return nil, err
	}

	var result []exchange.Trade
	for _, t := range trades {
		price, _ := strconv.ParseFloat(t.Price, 64)
		qty, _ := strconv.ParseFloat(t.Qty, 64)
		side := "buy"
		if t.IsBuyerMaker {
			side = "sell"
		}
		result = append(result, exchange.Trade{
			ID:        strconv.FormatInt(t.ID, 10),
			Symbol:    symbol,
			Side:      side,
			Price:     price,
			Volume:    qty,
			Timestamp: t.Time,
			Exchange:  "binance",
		})
	}
	return result, nil
}

func (c *BinanceSpotClient) FetchBalance(ctx context.Context, asset string) (*exchange.Balance, error) {
	params := map[string]string{
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": strconv.Itoa(c.recvWindow),
	}
	query := signParams(params, c.secret)
	ep := fmt.Sprintf("%s/api/v3/account?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-MBX-APIKEY", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var account struct {
		Balances []binanceBalanceResp `json:"balances"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&account); err != nil {
		return nil, err
	}

	for _, b := range account.Balances {
		if b.Asset != asset {
			continue
		}
		free, _ := strconv.ParseFloat(b.Free, 64)
		locked, _ := strconv.ParseFloat(b.Locked, 64)
		return &exchange.Balance{Asset: asset, Free: free, Locked: locked, Exchange: "binance"}, nil
	}
	return &exchange.Balance{Asset: asset, Free: 0, Locked: 0, Exchange: "binance"}, nil
}

func (c *BinanceSpotClient) FetchAllBalances(ctx context.Context) ([]exchange.Balance, error) {
	params := map[string]string{
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": strconv.Itoa(c.recvWindow),
	}
	query := signParams(params, c.secret)
	ep := fmt.Sprintf("%s/api/v3/account?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-MBX-APIKEY", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var account struct {
		Balances []binanceBalanceResp `json:"balances"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&account); err != nil {
		return nil, err
	}

	var balances []exchange.Balance
	for _, b := range account.Balances {
		free, _ := strconv.ParseFloat(b.Free, 64)
		locked, _ := strconv.ParseFloat(b.Locked, 64)
		if free > 0 || locked > 0 {
			balances = append(balances, exchange.Balance{Asset: b.Asset, Free: free, Locked: locked, Exchange: "binance"})
		}
	}
	return balances, nil
}

func (c *BinanceSpotClient) PlaceMarketOrder(ctx context.Context, symbol string, side string, volume float64) (*exchange.Order, error) {
	params := map[string]string{
		"symbol":    symbol,
		"side":      side,
		"type":      "MARKET",
		"quantity":  fmt.Sprintf("%.8f", volume),
		"timestamp": strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": strconv.Itoa(c.recvWindow),
	}
	body := signParams(params, c.secret)
	ep := fmt.Sprintf("%s/api/v3/order", c.baseURL)

	req, err := http.NewRequestWithContext(ctx, "POST", ep, strings.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-MBX-APIKEY", c.apiKey)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var order binanceOrderResp
	if err := json.NewDecoder(resp.Body).Decode(&order); err != nil {
		return nil, err
	}
	return binanceOrderToExchange(order), nil
}

func (c *BinanceSpotClient) PlaceLimitOrder(ctx context.Context, symbol string, side string, price, volume float64) (*exchange.Order, error) {
	params := map[string]string{
		"symbol":     symbol,
		"side":       side,
		"type":       "LIMIT",
		"price":      fmt.Sprintf("%.8f", price),
		"quantity":   fmt.Sprintf("%.8f", volume),
		"timeInForce": "GTC",
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": strconv.Itoa(c.recvWindow),
	}
	body := signParams(params, c.secret)
	ep := fmt.Sprintf("%s/api/v3/order", c.baseURL)

	req, err := http.NewRequestWithContext(ctx, "POST", ep, strings.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-MBX-APIKEY", c.apiKey)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var order binanceOrderResp
	if err := json.NewDecoder(resp.Body).Decode(&order); err != nil {
		return nil, err
	}
	return binanceOrderToExchange(order), nil
}

func (c *BinanceSpotClient) CancelOrder(ctx context.Context, symbol string, orderID string) error {
	oid, _ := strconv.ParseInt(orderID, 10, 64)
	params := map[string]string{
		"symbol":     symbol,
		"orderId":    strconv.FormatInt(oid, 10),
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": strconv.Itoa(c.recvWindow),
	}
	body := signParams(params, c.secret)
	ep := fmt.Sprintf("%s/api/v3/order", c.baseURL)

	req, err := http.NewRequestWithContext(ctx, "DELETE", ep, strings.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("X-MBX-APIKEY", c.apiKey)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}

func (c *BinanceSpotClient) FetchOpenOrders(ctx context.Context, symbol string) ([]exchange.Order, error) {
	params := map[string]string{
		"symbol":     symbol,
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": strconv.Itoa(c.recvWindow),
	}
	query := signParams(params, c.secret)
	ep := fmt.Sprintf("%s/api/v3/openOrders?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-MBX-APIKEY", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var orders []binanceOrderResp
	if err := json.NewDecoder(resp.Body).Decode(&orders); err != nil {
		return nil, err
	}

	var result []exchange.Order
	for _, o := range orders {
		result = append(result, *binanceOrderToExchange(o))
	}
	return result, nil
}

func (c *BinanceSpotClient) FetchOrderHistory(ctx context.Context, symbol string, limit int) ([]exchange.Order, error) {
	params := map[string]string{
		"symbol":     symbol,
		"limit":      strconv.Itoa(limit),
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": strconv.Itoa(c.recvWindow),
	}
	query := signParams(params, c.secret)
	ep := fmt.Sprintf("%s/api/v3/allOrders?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-MBX-APIKEY", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var orders []binanceOrderResp
	if err := json.NewDecoder(resp.Body).Decode(&orders); err != nil {
		return nil, err
	}

	var result []exchange.Order
	for _, o := range orders {
		result = append(result, *binanceOrderToExchange(o))
	}
	return result, nil
}

func signParams(params map[string]string, secret string) string {
	var keys []string
	for k := range params {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	var parts []string
	for _, k := range keys {
		parts = append(parts, url.QueryEscape(k)+"="+url.QueryEscape(params[k]))
	}
	queryString := strings.Join(parts, "&")

	h := hmac.New(sha256.New, []byte(secret))
	h.Write([]byte(queryString))
	signature := fmt.Sprintf("%x", h.Sum(nil))

	return queryString + "&signature=" + signature
}

func binanceOrderToExchange(o binanceOrderResp) *exchange.Order {
	price, _ := strconv.ParseFloat(o.Price, 64)
	qty, _ := strconv.ParseFloat(o.OrigQty, 64)
	filled, _ := strconv.ParseFloat(o.ExecutedQty, 64)
	return &exchange.Order{
		ID:           strconv.FormatInt(o.OrderID, 10),
		Exchange:     "binance",
		Symbol:       o.Symbol,
		Side:         o.Side,
		OrderType:    o.Type,
		Price:        price,
		Volume:       qty,
		FilledVolume: filled,
		Status:       o.Status,
		CreatedAt:    o.CreateTime,
		UpdatedAt:    o.UpdateTime,
	}
}