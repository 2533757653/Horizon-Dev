package hyperliquid

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"horizon/internal/exchange"
)

type HyperliquidClient struct {
	walletAddress string
	privateKey    string
	baseURL       string
	httpClient    *http.Client
}

type AdapterConfig struct {
	WalletAddress string
	PrivateKey    string
}

func NewClient(cfg AdapterConfig) *HyperliquidClient {
	return &HyperliquidClient{
		walletAddress: cfg.WalletAddress,
		privateKey:    cfg.PrivateKey,
		baseURL:       "https://api.hyperliquid.xyz",
		httpClient:    &http.Client{Timeout: 10 * time.Second},
	}
}

func (c *HyperliquidClient) Name() string   { return "hyperliquid" }
func (c *HyperliquidClient) IsEnabled() bool { return c.walletAddress != "" }

func (c *HyperliquidClient) FetchTicker(ctx context.Context, symbol string) (*exchange.Ticker, error) {
	ep := fmt.Sprintf("%s/v2/market", c.baseURL)
	payload := map[string]interface{}{
		"type":   "ticker",
		"symbol": symbol,
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

	var ticker struct {
		Data struct {
			LastPrice float64 `json:"lastPrice"`
			Volume24h float64 `json:"volume"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ticker); err != nil {
		return nil, err
	}

	return &exchange.Ticker{
		Symbol:    symbol,
		Price:     ticker.Data.LastPrice,
		Volume24h: ticker.Data.Volume24h,
		Exchange:  "hyperliquid",
		UpdatedAt: time.Now().Unix(),
	}, nil
}

func (c *HyperliquidClient) FetchOrderBook(ctx context.Context, symbol string, depth int) (*exchange.OrderBook, error) {
	ep := fmt.Sprintf("%s/v2/market", c.baseURL)
	payload := map[string]interface{}{
		"type":   "book",
		"symbol": symbol,
		"depth":  depth,
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

	var ob struct {
		Data struct {
			Bids [][]interface{} `json:"bids"`
			Asks [][]interface{} `json:"asks"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ob); err != nil {
		return nil, err
	}

	var book exchange.OrderBook
	book.Symbol = symbol
	book.Exchange = "hyperliquid"
	for _, b := range ob.Data.Bids {
		price, _ := strconv.ParseFloat(fmt.Sprintf("%v", b[0]), 64)
		size, _ := strconv.ParseFloat(fmt.Sprintf("%v", b[1]), 64)
		book.Bids = append(book.Bids, exchange.OrderBookEntry{Price: price, Size: size})
	}
	for _, a := range ob.Data.Asks {
		price, _ := strconv.ParseFloat(fmt.Sprintf("%v", a[0]), 64)
		size, _ := strconv.ParseFloat(fmt.Sprintf("%v", a[1]), 64)
		book.Asks = append(book.Asks, exchange.OrderBookEntry{Price: price, Size: size})
	}
	return &book, nil
}

func (c *HyperliquidClient) FetchTrades(ctx context.Context, symbol string, limit int) ([]exchange.Trade, error) {
	ep := fmt.Sprintf("%s/v2/market", c.baseURL)
	payload := map[string]interface{}{
		"type":   "user_fills",
		"symbol": symbol,
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

	var tradesResp struct {
		Data []struct {
			ID        string  `json:"hash"`
			Side      string  `json:"side"`
			Price     float64 `json:"price"`
			Volume    float64 `json:"size"`
			Timestamp int64   `json:"time"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tradesResp); err != nil {
		return nil, err
	}

	var trades []exchange.Trade
	for _, t := range tradesResp.Data {
		trades = append(trades, exchange.Trade{
			ID:        t.ID,
			Symbol:    symbol,
			Side:      t.Side,
			Price:     t.Price,
			Volume:    t.Volume,
			Timestamp: t.Timestamp,
			Exchange:  "hyperliquid",
		})
	}
	return trades, nil
}

func (c *HyperliquidClient) FetchBalance(ctx context.Context, asset string) (*exchange.Balance, error) {
	payload := map[string]interface{}{
		"type": "spotUserState",
		"user": c.walletAddress,
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/v2/state", bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var state struct {
		Data struct {
			Balances map[string]float64 `json:"balances"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&state); err != nil {
		return nil, err
	}

	balance := state.Data.Balances[asset]
	return &exchange.Balance{Asset: asset, Free: balance, Locked: 0, Exchange: "hyperliquid"}, nil
}

func (c *HyperliquidClient) FetchAllBalances(ctx context.Context) ([]exchange.Balance, error) {
	payload := map[string]interface{}{
		"type": "spotUserState",
		"user": c.walletAddress,
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/v2/state", bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var state struct {
		Data struct {
			Balances map[string]float64 `json:"balances"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&state); err != nil {
		return nil, err
	}

	var balances []exchange.Balance
	for asset, bal := range state.Data.Balances {
		balances = append(balances, exchange.Balance{Asset: asset, Free: bal, Locked: 0, Exchange: "hyperliquid"})
	}
	return balances, nil
}

func (c *HyperliquidClient) PlaceMarketOrder(ctx context.Context, symbol string, side string, volume float64) (*exchange.Order, error) {
	payload := map[string]interface{}{
		"type": "ORDER",
		"data": map[string]interface{}{
			"symbol":    symbol,
			"side":      side,
			"orderType": "M",
			"sz":        fmt.Sprintf("%.8f", volume),
			"user":      c.walletAddress,
		},
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/v2/order", bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var orderResp struct {
		Data struct {
			OrderID string `json:"orderId"`
			Status  string `json:"status"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&orderResp); err != nil {
		return nil, err
	}

	return &exchange.Order{
		ID:        orderResp.Data.OrderID,
		Exchange:  "hyperliquid",
		Symbol:    symbol,
		Side:      side,
		OrderType: "market",
		Volume:    volume,
		Status:    orderResp.Data.Status,
		CreatedAt: time.Now().Unix(),
		UpdatedAt: time.Now().Unix(),
	}, nil
}

func (c *HyperliquidClient) PlaceLimitOrder(ctx context.Context, symbol string, side string, price, volume float64) (*exchange.Order, error) {
	payload := map[string]interface{}{
		"type": "ORDER",
		"data": map[string]interface{}{
			"symbol":    symbol,
			"side":      side,
			"orderType": "LMT",
			"px":        fmt.Sprintf("%.8f", price),
			"sz":        fmt.Sprintf("%.8f", volume),
			"user":      c.walletAddress,
		},
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/v2/order", bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var orderResp struct {
		Data struct {
			OrderID string `json:"orderId"`
			Status  string `json:"status"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&orderResp); err != nil {
		return nil, err
	}

	return &exchange.Order{
		ID:        orderResp.Data.OrderID,
		Exchange:  "hyperliquid",
		Symbol:    symbol,
		Side:      side,
		OrderType: "limit",
		Price:     price,
		Volume:    volume,
		Status:    orderResp.Data.Status,
		CreatedAt: time.Now().Unix(),
		UpdatedAt: time.Now().Unix(),
	}, nil
}

func (c *HyperliquidClient) CancelOrder(ctx context.Context, symbol string, orderID string) error {
	payload := map[string]interface{}{
		"type": "CANCEL",
		"data": map[string]interface{}{
			"orderId": orderID,
			"symbol":  symbol,
			"user":    c.walletAddress,
		},
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/v2/order", bytes.NewReader(payloadBytes))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}

func (c *HyperliquidClient) FetchOpenOrders(ctx context.Context, symbol string) ([]exchange.Order, error) {
	payload := map[string]interface{}{
		"type": "ORDER",
		"data": map[string]interface{}{
			"type": "open",
			"user": c.walletAddress,
		},
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/v2/order", bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ordersResp struct {
		Data []struct {
			OrderID   string  `json:"orderId"`
			Symbol    string  `json:"symbol"`
			Side      string  `json:"side"`
			Price     float64 `json:"price"`
			Volume    float64 `json:"sz"`
			Filled    float64 `json:"filled"`
			Status    string  `json:"status"`
			CreatedAt int64   `json:"time"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ordersResp); err != nil {
		return nil, err
	}

	var orders []exchange.Order
	for _, o := range ordersResp.Data {
		orders = append(orders, exchange.Order{
			ID:           o.OrderID,
			Exchange:     "hyperliquid",
			Symbol:       o.Symbol,
			Side:         o.Side,
			OrderType:    "limit",
			Price:        o.Price,
			Volume:       o.Volume,
			FilledVolume: o.Filled,
			Status:       o.Status,
			CreatedAt:    o.CreatedAt,
			UpdatedAt:    time.Now().Unix(),
		})
	}
	return orders, nil
}

func (c *HyperliquidClient) FetchOrderHistory(ctx context.Context, symbol string, limit int) ([]exchange.Order, error) {
	payload := map[string]interface{}{
		"type": "ORDER",
		"data": map[string]interface{}{
			"type":  "history",
			"user":  c.walletAddress,
			"limit": limit,
		},
	}
	payloadBytes, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/v2/order", bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var ordersResp struct {
		Data []struct {
			OrderID   string  `json:"orderId"`
			Symbol    string  `json:"symbol"`
			Side      string  `json:"side"`
			Price     float64 `json:"price"`
			Volume    float64 `json:"sz"`
			Filled    float64 `json:"filled"`
			Status    string  `json:"status"`
			CreatedAt int64   `json:"time"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&ordersResp); err != nil {
		return nil, err
	}

	var orders []exchange.Order
	for _, o := range ordersResp.Data {
		orders = append(orders, exchange.Order{
			ID:           o.OrderID,
			Exchange:     "hyperliquid",
			Symbol:       o.Symbol,
			Side:         o.Side,
			OrderType:    "limit",
			Price:        o.Price,
			Volume:       o.Volume,
			FilledVolume: o.Filled,
			Status:       o.Status,
			CreatedAt:    o.CreatedAt,
			UpdatedAt:    time.Now().Unix(),
		})
	}
	return orders, nil
}