package portfolio

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"horizon/internal/exchange"
	"horizon/internal/marketdata"
)

type Tracker struct {
	registry *exchange.Registry
	db       *sql.DB
	fetcher  *marketdata.Fetcher
	balances map[string]map[string]*exchange.Balance // exchange -> asset -> balance
	mu       sync.RWMutex
}

type PortfolioSnapshot struct {
	Exchanges map[string][]exchange.Balance `json:"exchanges"`
	TotalUSDT float64                       `json:"total_usdt"`
	UpdatedAt int64                          `json:"updated_at"`
}

func NewTracker(registry *exchange.Registry, db *sql.DB) *Tracker {
	return &Tracker{
		registry: registry,
		db:       db,
		balances: make(map[string]map[string]*exchange.Balance),
	}
}

func (t *Tracker) Start(ctx context.Context) {
	t.refreshBalances(ctx)

	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				t.refreshBalances(ctx)
			}
		}
	}()
}

func (t *Tracker) refreshBalances(ctx context.Context) {
	t.mu.Lock()
	defer t.mu.Unlock()

	for _, adapter := range t.registry.List() {
		if !adapter.IsEnabled() {
			continue
		}
		balances, err := adapter.FetchAllBalances(ctx)
		if err != nil {
			slog.Warn("failed to fetch balances", "exchange", adapter.Name(), "error", err)
			continue
		}

		if t.balances[adapter.Name()] == nil {
			t.balances[adapter.Name()] = make(map[string]*exchange.Balance)
		}
		for i := range balances {
			bal := &balances[i]
			t.balances[adapter.Name()][bal.Asset] = bal
		}
	}
}

func (t *Tracker) GetSnapshot(ctx context.Context) *PortfolioSnapshot {
	t.mu.RLock()
	defer t.mu.RUnlock()

	snapshot := &PortfolioSnapshot{
		Exchanges: make(map[string][]exchange.Balance),
		UpdatedAt: time.Now().Unix(),
	}

	var totalUSDT float64
	for exch, balances := range t.balances {
		for _, bal := range balances {
			snapshot.Exchanges[exch] = append(snapshot.Exchanges[exch], *bal)
			// Get USDT value from market data
			if t.fetcher != nil {
				tickers := t.fetcher.GetTickers(bal.Asset + "USDT")
				var price float64
				for _, ticker := range tickers {
					price = ticker.Price
					break
				}
				totalUSDT += bal.Free*price + bal.Locked*price
			}
		}
	}

	snapshot.TotalUSDT = totalUSDT
	return snapshot
}

func (t *Tracker) SaveSnapshot(ctx context.Context) error {
	snapshot := t.GetSnapshot(ctx)

	for exch, balances := range snapshot.Exchanges {
		for _, bal := range balances {
			_, err := t.db.ExecContext(ctx,
				`INSERT INTO portfolio_snapshots (exchange, asset, free_balance, locked_balance, usdt_value)
				 VALUES (?, ?, ?, ?, ?)`,
				exch, bal.Asset, bal.Free, bal.Locked, bal.USDTValue)
			if err != nil {
				slog.Error("failed to save portfolio snapshot", "error", err)
				return fmt.Errorf("portfolio snapshot: %w", err)
			}
		}
	}
	return nil
}