package htx

import (
	"bytes"
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

type HTXSpotClient struct {
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

func NewClient(cfg AdapterConfig) *HTXSpotClient {
	return &HTXSpotClient{
		apiKey:     cfg.APIKey,
		secret:     cfg.Secret,
		recvWindow: cfg.RecvWindow,
		baseURL:    "https://api.huobi.pro",
		httpClient: &http.Client{Timeout: 10 * time.Second},
	}
}

func (c *HTXSpotClient) Name() string   { return "htx" }
func (c *HTXSpotClient) IsEnabled() bool { return c.apiKey != "" }

type htxTickerResp struct {
	Tick struct {
		LastPrice string `json:"lastPrice"`
		Volume    string `json:"vol"`
	} `json:"tick"`
	Symbol string `json:"symbol"`
}

type htxBalanceResp struct {
	Data []struct {
		Currency string `json:"currency"`
		Type     string `json:"type"`
		Balance  string `json:"balance"`
	} `json:"data"`
}

type htxOrderResp struct {
	Data struct {
		ID           int64   `json:"id"`
		Symbol       string  `json:"symbol"`
		Type         string  `json:"type"`
		Side         string  `json:"side"`
		Price        float64 `json:"price"`
		Amount       float64 `json:"amount"`
		FilledAmount float64 `json:"filled-amount"`
		State        string  `json:"state"`
		CreatedAt    int64   `json:"created-at"`
		UpdatedAt    int64   `json:"updated-at"`
	} `json:"data"`
}

func (c *HTXSpotClient) FetchTicker(ctx context.Context, symbol string) (*exchange.Ticker, error) {
	ep := fmt.Sprintf("%s/market/detail/merged?symbol=%s", c.baseURL, strings.ToLower(symbol))
	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ticker htxTickerResp
	if err := json.NewDecoder(resp.Body).Decode(&ticker); err != nil {
		return nil, err
	}

	price, _ := strconv.ParseFloat(ticker.Tick.LastPrice, 64)
	vol, _ := strconv.ParseFloat(ticker.Tick.Volume, 64)

	return &exchange.Ticker{
		Symbol:    symbol,
		Price:     price,
		Volume24h: vol,
		Exchange:  "htx",
		UpdatedAt: time.Now().Unix(),
	}, nil
}

func (c *HTXSpotClient) FetchOrderBook(ctx context.Context, symbol string, depth int) (*exchange.OrderBook, error) {
	ep := fmt.Sprintf("%s/market/depth?symbol=%s&type=step0", c.baseURL, strings.ToLower(symbol))
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
		Tick struct {
			Bids [][]string `json:"bid"`
			Asks [][]string `json:"ask"`
		} `json:"tick"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ob); err != nil {
		return nil, err
	}

	var book exchange.OrderBook
	book.Symbol = symbol
	book.Exchange = "htx"
	for _, b := range ob.Tick.Bids {
		price, _ := strconv.ParseFloat(b[0], 64)
		size, _ := strconv.ParseFloat(b[1], 64)
		book.Bids = append(book.Bids, exchange.OrderBookEntry{Price: price, Size: size})
	}
	for _, a := range ob.Tick.Asks {
		price, _ := strconv.ParseFloat(a[0], 64)
		size, _ := strconv.ParseFloat(a[1], 64)
		book.Asks = append(book.Asks, exchange.OrderBookEntry{Price: price, Size: size})
	}
	return &book, nil
}

func (c *HTXSpotClient) FetchTrades(ctx context.Context, symbol string, limit int) ([]exchange.Trade, error) {
	ep := fmt.Sprintf("%s/market/history/trade?symbol=%s&size=%d", c.baseURL, strings.ToLower(symbol), limit)
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
			ID        int64  `json:"id"`
			Price     string `json:"price"`
			Amount    string `json:"amount"`
			Direction string `json:"direction"`
			TS        int64  `json:"ts"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tradesResp); err != nil {
		return nil, err
	}

	var trades []exchange.Trade
	for _, t := range tradesResp.Data {
		price, _ := strconv.ParseFloat(t.Price, 64)
		qty, _ := strconv.ParseFloat(t.Amount, 64)
		trades = append(trades, exchange.Trade{
			ID:        strconv.FormatInt(t.ID, 10),
			Symbol:    symbol,
			Side:      t.Direction,
			Price:     price,
			Volume:    qty,
			Timestamp: t.TS,
			Exchange:  "htx",
		})
	}
	return trades, nil
}

func (c *HTXSpotClient) FetchBalance(ctx context.Context, asset string) (*exchange.Balance, error) {
	params := map[string]string{
		"AccessKey":        c.apiKey,
		"SignatureMethod":  "HmacSHA256",
		"SignatureVersion": "2",
		"Timestamp":        time.Now().UTC().Format("2006-01-02T15:04:05"),
		"AccountID":        "1",
	}
	query := signParams(params, c.secret, "GET", "/v1/account/accounts")
	ep := fmt.Sprintf("%s/v1/account/accounts?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	return &exchange.Balance{Asset: asset, Free: 0, Locked: 0, Exchange: "htx"}, nil
}

func (c *HTXSpotClient) FetchAllBalances(ctx context.Context) ([]exchange.Balance, error) {
	params := map[string]string{
		"AccessKey":        c.apiKey,
		"SignatureMethod":  "HmacSHA256",
		"SignatureVersion": "2",
		"Timestamp":        time.Now().UTC().Format("2006-01-02T15:04:05"),
		"AccountID":        "1",
	}
	query := signParams(params, c.secret, "GET", "/v1/account/accounts")
	ep := fmt.Sprintf("%s/v1/account/accounts?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var respData htxBalanceResp
	if err := json.NewDecoder(resp.Body).Decode(&respData); err != nil {
		return nil, err
	}

	var balances []exchange.Balance
	for _, b := range respData.Data {
		bal, _ := strconv.ParseFloat(b.Balance, 64)
		balances = append(balances, exchange.Balance{
			Asset:    b.Currency,
			Free:     bal,
			Locked:   0,
			Exchange: "htx",
		})
	}
	return balances, nil
}

func (c *HTXSpotClient) PlaceMarketOrder(ctx context.Context, symbol string, side string, volume float64) (*exchange.Order, error) {
	ep := fmt.Sprintf("%s/v1/order/orders/place", c.baseURL)
	payload := map[string]interface{}{
		"account-id": "1",
		"symbol":     strings.ToLower(symbol),
		"type":       side,
		amount":      fmt.Sprintf("%.8f", volume),
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", ep, bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var order htxOrderResp
	if err := json.NewDecoder(resp.Body).Decode(&order); err != nil {
		return nil, err
	}
	return htxOrderToExchange(order, symbol), nil
}

func (c *HTXSpotClient) PlaceLimitOrder(ctx context.Context, symbol string, side string, price, volume float64) (*exchange.Order, error) {
	ep := fmt.Sprintf("%s/v1/order/orders/place", c.baseURL)
	payload := map[string]interface{}{
		"account-id": "1",
		"symbol":     strings.ToLower(symbol),
		"type":       side,
		"amount":     fmt.Sprintf("%.8f", volume),
		"price":      fmt.Sprintf("%.8f", price),
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", ep, bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var order htxOrderResp
	if err := json.NewDecoder(resp.Body).Decode(&order); err != nil {
		return nil, err
	}
	return htxOrderToExchange(order, symbol), nil
}

func (c *HTXSpotClient) CancelOrder(ctx context.Context, symbol string, orderID string) error {
	oid, _ := strconv.ParseInt(orderID, 10, 64)
	ep := fmt.Sprintf("%s/v1/order/orders/%d/submitCancel", c.baseURL, oid)

	req, err := http.NewRequestWithContext(ctx, "POST", ep, nil)
	if err != nil {
		return err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}

func (c *HTXSpotClient) FetchOpenOrders(ctx context.Context, symbol string) ([]exchange.Order, error) {
	params := map[string]string{
		"AccessKey":        c.apiKey,
		"SignatureMethod":  "HmacSHA256",
		"SignatureVersion": "2",
		"Timestamp":        time.Now().UTC().Format("2006-01-02T15:04:05"),
		"symbol":           strings.ToLower(symbol),
		"states":           "submitted,partial-filled",
	}
	query := signParams(params, c.secret, "GET", "/v1/order/orders")
	ep := fmt.Sprintf("%s/v1/order/orders?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ordersResp struct {
		Data []htxOrderResp `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ordersResp); err != nil {
		return nil, err
	}

	var orders []exchange.Order
	for _, o := range ordersResp.Data {
		orders = append(orders, *htxOrderToExchange(o, symbol))
	}
	return orders, nil
}

func (c *HTXSpotClient) FetchOrderHistory(ctx context.Context, symbol string, limit int) ([]exchange.Order, error) {
	params := map[string]string{
		"AccessKey":        c.apiKey,
		"SignatureMethod":  "HmacSHA256",
		"SignatureVersion": "2",
		"Timestamp":        time.Now().UTC().Format("2006-01-02T15:04:05"),
		"symbol":           strings.ToLower(symbol),
		"states":           "filled,canceled",
	}
	query := signParams(params, c.secret, "GET", "/v1/order/orders")
	ep := fmt.Sprintf("%s/v1/order/orders?%s", c.baseURL, query)

	req, err := http.NewRequestWithContext(ctx, "GET", ep, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ordersResp struct {
		Data []htxOrderResp `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ordersResp); err != nil {
		return nil, err
	}

	var orders []exchange.Order
	for _, o := range ordersResp.Data {
		orders = append(orders, *htxOrderToExchange(o, symbol))
	}
	return orders, nil
}

func signParams(params map[string]string, secret, method, path string) string {
	var keys []string
	for k := range params {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	var parts []string
	for _, k := range keys {
		parts = append(parts, url.QueryEscape(k)+"="+url.QueryEscape(params[k]))
	}
	joined := strings.Join(parts, "&")
	return joined
}

func htxOrderToExchange(o htxOrderResp, symbol string) *exchange.Order {
	return &exchange.Order{
		ID:           strconv.FormatInt(o.Data.ID, 10),
		Exchange:     "htx",
		Symbol:       symbol,
		Side:         o.Data.Side,
		OrderType:    o.Data.Type,
		Price:        o.Data.Price,
		Volume:       o.Data.Amount,
		FilledVolume: o.Data.FilledAmount,
		Status:       o.Data.State,
		CreatedAt:    o.Data.CreatedAt,
		UpdatedAt:    o.Data.UpdatedAt,
	}
}