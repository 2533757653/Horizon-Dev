package bitget

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"horizon/internal/exchange"
)

type BitgetSpotClient struct {
	key        string
	secret     string
	pass       string
	baseURL    string
	httpClient *http.Client
}

type AdapterConfig struct {
	Key    string
	Secret string
	Pass   string
}

func NewClient(cfg AdapterConfig) *BitgetSpotClient {
	return &BitgetSpotClient{
		key:        cfg.Key,
		secret:     cfg.Secret,
		pass:       cfg.Pass,
		baseURL:    "https://api.bitget.com",
		httpClient: &http.Client{Timeout: 10 * time.Second},
	}
}

func (c *BitgetSpotClient) Name() string    { return "bitget" }
func (c *BitgetSpotClient) IsEnabled() bool { return c.key != "" }

type bgTickerResp struct {
	Data struct {
		LastPrice string `json:"lastPr"`
		Volume24h string `json:"vol24h"`
	} `json:"data"`
	Symbol string `json:"symbol"`
}

type bgBalanceResp struct {
	Data []struct {
		CoinName string `json:"coinName"`
		Free     string `json:"free"`
		Locked   string `json:"locked"`
	} `json:"data"`
}

type bgOrderResp struct {
	Data struct {
		OrderID    string `json:"orderId"`
		Symbol     string `json:"symbol"`
		Side       string `json:"side"`
		OrderType  string `json:"orderType"`
		Price      string `json:"price"`
		Size       string `json:"size"`
		FillSize   string `json:"fillSize"`
		Status     string `json:"status"`
		CreateTime string `json:"cTime"`
		UpdateTime string `json:"uTime"`
	} `json:"data"`
}

func (c *BitgetSpotClient) FetchTicker(ctx context.Context, symbol string) (*exchange.Ticker, error) {
	ep := fmt.Sprintf("%s/api/v2/spot/market/ticker?symbol=%s", c.baseURL, symbol)
	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ticker bgTickerResp
	if err := json.NewDecoder(resp.Body).Decode(&ticker); err != nil {
		return nil, err
	}

	price, _ := strconv.ParseFloat(ticker.Data.LastPrice, 64)
	vol, _ := strconv.ParseFloat(ticker.Data.Volume24h, 64)

	return &exchange.Ticker{
		Symbol:    symbol,
		Price:     price,
		Volume24h: vol,
		Exchange:  "bitget",
		UpdatedAt: time.Now().Unix(),
	}, nil
}

func (c *BitgetSpotClient) FetchOrderBook(ctx context.Context, symbol string, depth int) (*exchange.OrderBook, error) {
	ep := fmt.Sprintf("%s/api/v2/spot/market/books?symbol=%s&limit=%d", c.baseURL, symbol, depth)
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
		Data struct {
			Bids [][]string `json:"bids"`
			Asks [][]string `json:"asks"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ob); err != nil {
		return nil, err
	}

	var book exchange.OrderBook
	book.Symbol = symbol
	book.Exchange = "bitget"
	for _, b := range ob.Data.Bids {
		price, _ := strconv.ParseFloat(b[0], 64)
		size, _ := strconv.ParseFloat(b[1], 64)
		book.Bids = append(book.Bids, exchange.OrderBookEntry{Price: price, Size: size})
	}
	for _, a := range ob.Data.Asks {
		price, _ := strconv.ParseFloat(a[0], 64)
		size, _ := strconv.ParseFloat(a[1], 64)
		book.Asks = append(book.Asks, exchange.OrderBookEntry{Price: price, Size: size})
	}
	return &book, nil
}

func (c *BitgetSpotClient) FetchTrades(ctx context.Context, symbol string, limit int) ([]exchange.Trade, error) {
	ep := fmt.Sprintf("%s/api/v2/spot/market/fills?symbol=%s&limit=%d", c.baseURL, symbol, limit)
	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var tradesResp struct {
		Data []struct {
			OrderID string `json:"orderId"`
			Price   string `json:"price"`
			Size    string `json:"size"`
			Side    string `json:"side"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tradesResp); err != nil {
		return nil, err
	}

	var trades []exchange.Trade
	for _, t := range tradesResp.Data {
		price, _ := strconv.ParseFloat(t.Price, 64)
		size, _ := strconv.ParseFloat(t.Size, 64)
		trades = append(trades, exchange.Trade{
			ID:       t.OrderID,
			Symbol:   symbol,
			Side:     t.Side,
			Price:    price,
			Volume:   size,
			Exchange: "bitget",
		})
	}
	return trades, nil
}

func (c *BitgetSpotClient) FetchBalance(ctx context.Context, asset string) (*exchange.Balance, error) {
	params := map[string]string{
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": "5000",
	}
	query := signParams(params, "GET", "/api/v2/spot/account/assets", c.secret)
	ep := fmt.Sprintf("%s/api/v2/spot/account/assets?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("ACCESS-KEY", c.key)
	req.Header.Set("ACCESS-PASSPHRASE", c.pass)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var balResp bgBalanceResp
	if err := json.NewDecoder(resp.Body).Decode(&balResp); err != nil {
		return nil, err
	}

	for _, b := range balResp.Data {
		if b.CoinName != asset {
			continue
		}
		free, _ := strconv.ParseFloat(b.Free, 64)
		locked, _ := strconv.ParseFloat(b.Locked, 64)
		return &exchange.Balance{Asset: asset, Free: free, Locked: locked, Exchange: "bitget"}, nil
	}
	return &exchange.Balance{Asset: asset, Free: 0, Locked: 0, Exchange: "bitget"}, nil
}

func (c *BitgetSpotClient) FetchAllBalances(ctx context.Context) ([]exchange.Balance, error) {
	params := map[string]string{
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": "5000",
	}
	query := signParams(params, "GET", "/api/v2/spot/account/assets", c.secret)
	ep := fmt.Sprintf("%s/api/v2/spot/account/assets?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("ACCESS-KEY", c.key)
	req.Header.Set("ACCESS-PASSPHRASE", c.pass)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var balResp bgBalanceResp
	if err := json.NewDecoder(resp.Body).Decode(&balResp); err != nil {
		return nil, err
	}

	var balances []exchange.Balance
	for _, b := range balResp.Data {
		free, _ := strconv.ParseFloat(b.Free, 64)
		locked, _ := strconv.ParseFloat(b.Locked, 64)
		if free > 0 || locked > 0 {
			balances = append(balances, exchange.Balance{Asset: b.CoinName, Free: free, Locked: locked, Exchange: "bitget"})
		}
	}
	return balances, nil
}

func (c *BitgetSpotClient) PlaceMarketOrder(ctx context.Context, symbol string, side string, volume float64) (*exchange.Order, error) {
	ep := fmt.Sprintf("%s/api/v2/spot/trade/order", c.baseURL)
	body := map[string]interface{}{
		"symbol":    symbol,
		"side":      side,
		"orderType": "market",
		"size":      fmt.Sprintf("%.8f", volume),
		"timestamp": strconv.FormatInt(time.Now().UnixMilli(), 10),
	}
	bodyBytes, _ := json.Marshal(body)

	req, err := http.NewRequestWithContext(ctx, "POST", ep, bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("ACCESS-KEY", c.key)
	req.Header.Set("ACCESS-PASSPHRASE", c.pass)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var order bgOrderResp
	if err := json.NewDecoder(resp.Body).Decode(&order); err != nil {
		return nil, err
	}
	return bgOrderToExchange(order), nil
}

func (c *BitgetSpotClient) PlaceLimitOrder(ctx context.Context, symbol string, side string, price, volume float64) (*exchange.Order, error) {
	ep := fmt.Sprintf("%s/api/v2/spot/trade/order", c.baseURL)
	body := map[string]interface{}{
		"symbol":    symbol,
		"side":      side,
		"orderType": "limit",
		"price":     fmt.Sprintf("%.8f", price),
		"size":      fmt.Sprintf("%.8f", volume),
		"timestamp": strconv.FormatInt(time.Now().UnixMilli(), 10),
	}
	bodyBytes, _ := json.Marshal(body)

	req, err := http.NewRequestWithContext(ctx, "POST", ep, bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("ACCESS-KEY", c.key)
	req.Header.Set("ACCESS-PASSPHRASE", c.pass)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var order bgOrderResp
	if err := json.NewDecoder(resp.Body).Decode(&order); err != nil {
		return nil, err
	}
	return bgOrderToExchange(order), nil
}

func (c *BitgetSpotClient) CancelOrder(ctx context.Context, symbol string, orderID string) error {
	ep := fmt.Sprintf("%s/api/v2/spot/trade/cancel-order", c.baseURL)
	body := map[string]string{
		"orderId":   orderID,
		"symbol":    symbol,
		"timestamp": strconv.FormatInt(time.Now().UnixMilli(), 10),
	}
	bodyBytes, _ := json.Marshal(body)

	req, err := http.NewRequestWithContext(ctx, "POST", ep, bytes.NewReader(bodyBytes))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("ACCESS-KEY", c.key)
	req.Header.Set("ACCESS-PASSPHRASE", c.pass)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}

func (c *BitgetSpotClient) FetchOpenOrders(ctx context.Context, symbol string) ([]exchange.Order, error) {
	params := map[string]string{
		"symbol":     symbol,
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": "5000",
	}
	query := signParams(params, "GET", "/api/v2/spot/trade/open-orders", c.secret)
	ep := fmt.Sprintf("%s/api/v2/spot/trade/open-orders?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("ACCESS-KEY", c.key)
	req.Header.Set("ACCESS-PASSPHRASE", c.pass)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ordersResp struct {
		Data []bgOrderResp `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ordersResp); err != nil {
		return nil, err
	}

	var orders []exchange.Order
	for _, o := range ordersResp.Data {
		orders = append(orders, *bgOrderToExchange(o))
	}
	return orders, nil
}

func (c *BitgetSpotClient) FetchOrderHistory(ctx context.Context, symbol string, limit int) ([]exchange.Order, error) {
	params := map[string]string{
		"symbol":     symbol,
		"limit":      strconv.Itoa(limit),
		"timestamp":  strconv.FormatInt(time.Now().UnixMilli(), 10),
		"recvWindow": "5000",
	}
	query := signParams(params, "GET", "/api/v2/spot/trade/history-orders", c.secret)
	ep := fmt.Sprintf("%s/api/v2/spot/trade/history-orders?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("ACCESS-KEY", c.key)
	req.Header.Set("ACCESS-PASSPHRASE", c.pass)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ordersResp struct {
		Data []bgOrderResp `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ordersResp); err != nil {
		return nil, err
	}

	var orders []exchange.Order
	for _, o := range ordersResp.Data {
		orders = append(orders, *bgOrderToExchange(o))
	}
	return orders, nil
}

func signParams(params map[string]string, method, path, secret string) string {
	var keys []string
	for k := range params {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	var parts []string
	for _, k := range keys {
		parts = append(parts, k+"="+params[k])
	}
	joined := strings.Join(parts, "&")

	h := hmac.New(sha256.New, []byte(secret))
	h.Write([]byte(joined))
	signature := hex.EncodeToString(h.Sum(nil))
	return joined + "&signature=" + signature
}

func bgOrderToExchange(o bgOrderResp) *exchange.Order {
	price, _ := strconv.ParseFloat(o.Data.Price, 64)
	size, _ := strconv.ParseFloat(o.Data.Size, 64)
	filled, _ := strconv.ParseFloat(o.Data.FillSize, 64)
	cTime, _ := strconv.ParseInt(o.Data.CreateTime, 10, 64)
	uTime, _ := strconv.ParseInt(o.Data.UpdateTime, 10, 64)
	return &exchange.Order{
		ID:           o.Data.OrderID,
		Exchange:     "bitget",
		Symbol:       o.Data.Symbol,
		Side:         o.Data.Side,
		OrderType:    o.Data.OrderType,
		Price:        price,
		Volume:       size,
		FilledVolume: filled,
		Status:       o.Data.Status,
		CreatedAt:    cTime,
		UpdatedAt:    uTime,
	}
}
