package ordermanager

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

type Manager struct {
	registry   *exchange.Registry
	db         *sql.DB
	fetcher    *marketdata.Fetcher
	openOrders map[string]*exchange.Order // orderID -> order
	mu         sync.RWMutex
}

func NewManager(registry *exchange.Registry, db *sql.DB, fetcher *marketdata.Fetcher) *Manager {
	return &Manager{
		registry:   registry,
		db:         db,
		fetcher:    fetcher,
		openOrders: make(map[string]*exchange.Order),
	}
}

func (m *Manager) Start(ctx context.Context) {
	go m.syncOpenOrders(ctx)
}

func (m *Manager) syncOpenOrders(ctx context.Context) {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			m.refreshOpenOrders(ctx)
		}
	}
}

func (m *Manager) refreshOpenOrders(ctx context.Context) {
	m.mu.Lock()
	defer m.mu.Unlock()

	for _, adapter := range m.registry.List() {
		if !adapter.IsEnabled() {
			continue
		}
		orders, err := adapter.FetchOpenOrders(ctx, "")
		if err != nil {
			slog.Warn("failed to fetch open orders", "exchange", adapter.Name(), "error", err)
			continue
		}
		for _, order := range orders {
			m.openOrders[order.ID] = &order
		}
	}
}

func (m *Manager) StartExpirationLoop(ctx context.Context) {
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				m.checkExpirations()
			}
		}
	}()
}

func (m *Manager) checkExpirations() {
	// Check approved-but-not-submitted orders for expiration
	// Implementation depends on order state tracking (Step 3)
}

func (m *Manager) SubmitOrder(ctx context.Context, req exchange.OrderRequest) (*exchange.Order, error) {
	adapter, ok := m.registry.Get(req.Exchange)
	if !ok {
		return nil, fmt.Errorf("exchange not found: %s", req.Exchange)
	}

	var order *exchange.Order
	var err error

	if req.Type == "market" {
		order, err = adapter.PlaceMarketOrder(ctx, req.Symbol, req.Side, req.Volume)
	} else {
		order, err = adapter.PlaceLimitOrder(ctx, req.Symbol, req.Side, req.Price, req.Volume)
	}

	if err != nil {
		m.recordEvent(ctx, "", "rejected", fmt.Sprintf("exchange: %s", err.Error()))
		return nil, fmt.Errorf("order: %w", err)
	}

	m.mu.Lock()
	m.openOrders[order.ID] = order
	m.mu.Unlock()

	m.recordEvent(ctx, order.ID, "submitted", "")

	return order, nil
}

func (m *Manager) CancelOrder(ctx context.Context, orderID string, exch string) error {
	adapter, ok := m.registry.Get(exch)
	if !ok {
		return fmt.Errorf("exchange not found: %s", exch)
	}

	err := adapter.CancelOrder(ctx, "", orderID)
	if err != nil {
		return fmt.Errorf("cancel: %w", err)
	}

	m.mu.Lock()
	if order, ok := m.openOrders[orderID]; ok {
		order.Status = "cancelled"
	}
	m.mu.Unlock()

	m.recordEvent(ctx, orderID, "cancelled", "")
	return nil
}

func (m *Manager) GetOpenOrders(ctx context.Context, exchangeName string, symbol string) ([]exchange.Order, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var orders []exchange.Order
	for _, order := range m.openOrders {
		if exchangeName != "" && order.Exchange != exchangeName {
			continue
		}
		if symbol != "" && order.Symbol != symbol {
			continue
		}
		orders = append(orders, *order)
	}
	return orders, nil
}

func (m *Manager) GetOrderHistory(ctx context.Context, exchangeName string, symbol string, limit int) ([]exchange.Order, error) {
	if exchangeName != "" {
		adapter, ok := m.registry.Get(exchangeName)
		if !ok {
			return nil, fmt.Errorf("exchange not found: %s", exchangeName)
		}
		return adapter.FetchOrderHistory(ctx, symbol, limit)
	}

	var allOrders []exchange.Order
	for _, adapter := range m.registry.List() {
		if !adapter.IsEnabled() {
			continue
		}
		orders, err := adapter.FetchOrderHistory(ctx, symbol, limit)
		if err != nil {
			slog.Warn("failed to fetch order history", "exchange", adapter.Name(), "error", err)
			continue
		}
		allOrders = append(allOrders, orders...)
	}
	return allOrders, nil
}

func (m *Manager) recordEvent(ctx context.Context, orderID, eventType, eventData string) {
	_, err := m.db.ExecContext(ctx,
		"INSERT INTO order_events (order_id, event_type, event_data) VALUES (?, ?, ?)",
		orderID, eventType, eventData)
	if err != nil {
		slog.Error("failed to record order event", "error", err)
	}
}