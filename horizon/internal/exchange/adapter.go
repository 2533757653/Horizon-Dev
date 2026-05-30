package exchange

import "context"

type Adapter interface {
    Name() string
    IsEnabled() bool

    // Market Data
    FetchTicker(ctx context.Context, symbol string) (*Ticker, error)
    FetchOrderBook(ctx context.Context, symbol string, depth int) (*OrderBook, error)
    FetchTrades(ctx context.Context, symbol string, limit int) ([]Trade, error)

    // Account
    FetchBalance(ctx context.Context, asset string) (*Balance, error)
    FetchAllBalances(ctx context.Context) ([]Balance, error)

    // Orders
    PlaceMarketOrder(ctx context.Context, symbol string, side string, volume float64) (*Order, error)
    PlaceLimitOrder(ctx context.Context, symbol string, side string, price, volume float64) (*Order, error)
    CancelOrder(ctx context.Context, symbol string, orderID string) error
    FetchOpenOrders(ctx context.Context, symbol string) ([]Order, error)
    FetchOrderHistory(ctx context.Context, symbol string, limit int) ([]Order, error)
}