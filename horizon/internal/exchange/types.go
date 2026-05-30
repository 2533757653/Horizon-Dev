package exchange

type Ticker struct {
    Symbol    string  `json:"symbol"`
    Price     float64 `json:"price"`
    Volume24h float64 `json:"volume_24h"`
    Exchange  string  `json:"exchange"`
    UpdatedAt int64   `json:"updated_at"`
}

type OrderBook struct {
    Symbol   string           `json:"symbol"`
    Bids     []OrderBookEntry `json:"bids"`
    Asks     []OrderBookEntry `json:"asks"`
    Exchange string           `json:"exchange"`
}

type OrderBookEntry struct {
    Price float64 `json:"price"`
    Size  float64 `json:"size"`
}

type Trade struct {
    ID        string  `json:"id"`
    Symbol    string  `json:"symbol"`
    Side      string  `json:"side"`
    Price     float64 `json:"price"`
    Volume    float64 `json:"volume"`
    Timestamp int64   `json:"timestamp"`
    Exchange  string  `json:"exchange"`
}

type Balance struct {
    Asset     string  `json:"asset"`
    Free      float64 `json:"free"`
    Locked    float64 `json:"locked"`
    USDTValue float64 `json:"usdt_value"`
    Exchange  string  `json:"exchange"`
}

type Order struct {
    ID           string  `json:"id"`
    Exchange     string  `json:"exchange"`
    Symbol       string  `json:"symbol"`
    Side         string  `json:"side"`
    OrderType    string  `json:"order_type"`
    Price        float64 `json:"price"`
    Volume       float64 `json:"volume"`
    FilledVolume float64 `json:"filled_volume"`
    Status       string  `json:"status"`
    CreatedAt    int64   `json:"created_at"`
    UpdatedAt    int64   `json:"updated_at"`
}

type OrderRequest struct {
    Exchange string  `json:"exchange"`
    Symbol   string  `json:"symbol"`
    Side     string  `json:"side"`
    Type     string  `json:"order_type"`
    Price    float64 `json:"price"`
    Volume   float64 `json:"volume"`
}