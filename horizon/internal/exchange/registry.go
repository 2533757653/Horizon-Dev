package exchange

type Registry struct {
    adapters map[string]Adapter
}

func NewRegistry() *Registry {
    return &Registry{
        adapters: make(map[string]Adapter),
    }
}

func (r *Registry) Register(name string, adapter Adapter) {
    r.adapters[name] = adapter
}

func (r *Registry) Get(name string) (Adapter, bool) {
    adapter, ok := r.adapters[name]
    return adapter, ok
}

func (r *Registry) List() []Adapter {
    var list []Adapter
    for _, adapter := range r.adapters {
        list = append(list, adapter)
    }
    return list
}

func (r *Registry) GetAllTickers(ctx context.Context, symbol string) []*Ticker {
    var tickers []*Ticker
    for _, adapter := range r.adapters {
        if !adapter.IsEnabled() {
            continue
        }
        ticker, err := adapter.FetchTicker(ctx, symbol)
        if err != nil {
            continue
        }
        tickers = append(tickers, ticker)
    }
    return tickers
}