package web

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"horizon/internal/exchange"
	"horizon/internal/marketdata"
	"horizon/internal/ordermanager"
	"horizon/internal/portfolio"
)

//go:embed static/index.html
var indexHTML []byte

type Server struct {
	httpServer *http.Server
	orderMgr   *ordermanager.Manager
	fetcher    *marketdata.Fetcher
	portfolio  *portfolio.Tracker
}

func NewServer(host string, port int, orderMgr *ordermanager.Manager, fetcher *marketdata.Fetcher, portfolio *portfolio.Tracker) *Server {
	mux := http.NewServeMux()
	s := &Server{
		orderMgr:  orderMgr,
		fetcher:   fetcher,
		portfolio: portfolio,
	}

	mux.HandleFunc("GET /api/health", s.handleHealth)
	mux.HandleFunc("GET /api/portfolio", s.handlePortfolio)
	mux.HandleFunc("GET /api/marketdata/{symbol}", s.handleMarketData)
	mux.HandleFunc("GET /api/marketdata/stream", s.handleMarketDataStream)
	mux.HandleFunc("GET /api/orders/open", s.handleOpenOrders)
	mux.HandleFunc("POST /api/orders", s.handleSubmitOrder)
	mux.HandleFunc("DELETE /api/orders/{id}", s.handleCancelOrder)
	mux.HandleFunc("GET /", s.handleIndex)

	return &Server{
		httpServer: &http.Server{
			Addr:    fmt.Sprintf("%s:%d", host, port),
			Handler: mux,
		},
		orderMgr:  orderMgr,
		fetcher:   fetcher,
		portfolio: portfolio,
	}
}

func (s *Server) ListenAndServe() error {
	return s.httpServer.ListenAndServe()
}

func (s *Server) Shutdown(ctx context.Context) error {
	return s.httpServer.Shutdown(ctx)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (s *Server) handlePortfolio(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	snapshot := s.portfolio.GetSnapshot(ctx)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(snapshot)
}

func (s *Server) handleMarketData(w http.ResponseWriter, r *http.Request) {
	symbol := r.PathValue("symbol")
	ctx := r.Context()

	tickers := s.fetcher.GetTickers(symbol)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"symbol":  symbol,
		"tickers": tickers,
	})
}

func (s *Server) handleMarketDataStream(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "SSE not supported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	updateCh := make(chan *marketdata.MarketDataUpdate, 10)
	s.fetcher.Subscribe(updateCh)

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case update := <-updateCh:
			data, _ := json.Marshal(update)
			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()
		}
	}
}

func (s *Server) handleOpenOrders(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	exchangeName := r.URL.Query().Get("exchange")
	symbol := r.URL.Query().Get("symbol")

	orders, err := s.orderMgr.GetOpenOrders(ctx, exchangeName, symbol)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(orders)
}

func (s *Server) handleSubmitOrder(w http.ResponseWriter, r *http.Request) {
	var req exchange.OrderRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}

	ctx := r.Context()
	order, err := s.orderMgr.SubmitOrder(ctx, req)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(order)
}

func (s *Server) handleCancelOrder(w http.ResponseWriter, r *http.Request) {
	orderID := r.PathValue("id")
	exchangeName := r.URL.Query().Get("exchange")

	ctx := r.Context()
	if err := s.orderMgr.CancelOrder(ctx, orderID, exchangeName); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "cancelled"})
}

func (s *Server) handleIndex(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html")
	w.Write(indexHTML)
}