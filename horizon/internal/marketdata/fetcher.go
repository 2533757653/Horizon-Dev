package marketdata

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"horizon/internal/exchange"
)

type Fetcher struct {
	registry   *exchange.Registry
	interval   time.Duration
	tickers    map[string]map[string]*exchange.Ticker // symbol -> exchange -> ticker
	orderbooks map[string]map[string]*exchange.OrderBook
	mu          sync.RWMutex
	subscribers []chan<- *MarketDataUpdate
}

type MarketDataUpdate struct {
	Type     string      `json:"type"` // "ticker" or "orderbook"
	Exchange string      `json:"exchange"`
	Symbol   string      `json:"symbol"`
	Data     interface{} `json:"data"`
}

func NewFetcher(registry *exchange.Registry, intervalSeconds int) *Fetcher {
	return &Fetcher{
		registry:    registry,
		interval:    time.Duration(intervalSeconds) * time.Second,
		tickers:     make(map[string]map[string]*exchange.Ticker),
		orderbooks:  make(map[string]map[string]*exchange.OrderBook),
		subscribers: make([]chan<- *MarketDataUpdate, 0),
	}
}

func (f *Fetcher) Start(ctx context.Context) {
	go f.run(ctx)
}

func (f *Fetcher) run(ctx context.Context) {
	symbols := []string{"BTCUSDT", "ETHUSDT", "SOLUSDT"}

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		for _, symbol := range symbols {
			f.fetchSymbol(ctx, symbol)
		}

		time.Sleep(f.interval)
	}
}

func (f *Fetcher) fetchSymbol(ctx context.Context, symbol string) {
	for _, adapter := range f.registry.List() {
		if !adapter.IsEnabled() {
			continue
		}

		ticker, err := adapter.FetchTicker(ctx, symbol)
		if err != nil {
			slog.Warn("failed to fetch ticker",
				"exchange", adapter.Name(),
				"symbol", symbol,
				"error", err)
			continue
		}

		f.mu.Lock()
		if f.tickers[symbol] == nil {
			f.tickers[symbol] = make(map[string]*exchange.Ticker)
		}
		f.tickers[symbol][adapter.Name()] = ticker

		update := &MarketDataUpdate{
			Type:     "ticker",
			Exchange: adapter.Name(),
			Symbol:   symbol,
			Data:     ticker,
		}
		f.mu.Unlock()

		f.notifySubscribers(update)
	}
}

func (f *Fetcher) Subscribe(ch chan<- *MarketDataUpdate) {
	f.mu.Lock()
	f.subscribers = append(f.subscribers, ch)
	f.mu.Unlock()
}

func (f *Fetcher) notifySubscribers(update *MarketDataUpdate) {
	f.mu.RLock()
	subs := f.subscribers
	f.mu.RUnlock()

	for _, ch := range subs {
		select {
		case ch <- update:
		default:
		}
	}
}

func (f *Fetcher) GetTickers(symbol string) []*exchange.Ticker {
	f.mu.RLock()
	defer f.mu.RUnlock()

	var result []*exchange.Ticker
	if tickers, ok := f.tickers[symbol]; ok {
		for _, t := range tickers {
			result = append(result, t)
		}
	}
	return result
}