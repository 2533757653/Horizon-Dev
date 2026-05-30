package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"horizon/internal/config"
	"horizon/internal/database"
	"horizon/internal/exchange"
	"horizon/internal/exchange/binance"
	"horizon/internal/exchange/htx"
	"horizon/internal/exchange/hyperliquid"
	"horizon/internal/exchange/bitget"
	"horizon/internal/marketdata"
	"horizon/internal/ordermanager"
	"horizon/internal/portfolio"
	"horizon/internal/web"
)

func main() {
	jsonHandler := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo})
	slog.SetDefault(slog.New(jsonHandler))

	cfg, err := config.Load("configs/config.yaml")
	if err != nil {
		slog.Error("failed to load config", "error", err)
		os.Exit(1)
	}

	// Initialize database
	db, err := database.EnsureDB(cfg.Database.Path)
	if err != nil {
		slog.Error("failed to open database", "error", err)
		os.Exit(1)
	}
	defer db.Close()

	if err := database.RunMigrations(db, "internal/database/migrations/001_initial.sql"); err != nil {
		slog.Error("failed to run migrations", "error", err)
		os.Exit(1)
	}

	// Initialize exchange registry
	registry := exchange.NewRegistry()

	// Register Binance
	if cfg.Exchanges.Binance.Enabled {
		binanceAdapter := binance.NewClient(binance.AdapterConfig{
			APIKey:     cfg.Exchanges.Binance.APIKey,
			Secret:     cfg.Exchanges.Binance.Secret,
			RecvWindow: cfg.Exchanges.Binance.RecvWindow,
		})
		registry.Register("binance", binanceAdapter)
		slog.Info("registered binance exchange adapter")
	}

	// Register HTX
	if cfg.Exchanges.HTX.Enabled {
		htxAdapter := htx.NewClient(htx.AdapterConfig{
			APIKey:     cfg.Exchanges.HTX.APIKey,
			Secret:     cfg.Exchanges.HTX.Secret,
			RecvWindow: cfg.Exchanges.HTX.RecvWindow,
		})
		registry.Register("htx", htxAdapter)
		slog.Info("registered htx exchange adapter")
	}

	// Register Hyperliquid
	if cfg.Exchanges.Hyperliquid.Enabled {
		hlAdapter := hyperliquid.NewClient(hyperliquid.AdapterConfig{
			WalletAddress: cfg.Exchanges.Hyperliquid.WalletAddr,
			PrivateKey:     cfg.Exchanges.Hyperliquid.PrivateKey,
		})
		registry.Register("hyperliquid", hlAdapter)
		slog.Info("registered hyperliquid exchange adapter")
	}

	// Register Bitget
	if cfg.Exchanges.Bitget.Enabled {
		bgAdapter := bitget.NewClient(bitget.AdapterConfig{
			Key:    cfg.Exchanges.Bitget.APIKey,
			Secret: cfg.Exchanges.Bitget.Secret,
			Pass:   cfg.Exchanges.Bitget.WalletAddr, // password stored in wallet_addr field
		})
		registry.Register("bitget", bgAdapter)
		slog.Info("registered bitget exchange adapter")
	}

	// Initialize market data fetcher
	fetcher := marketdata.NewFetcher(registry, cfg.Trading.MarketDataPollInterval)

	// Initialize portfolio tracker
	portfolioTracker := portfolio.NewTracker(registry, db)
	portfolioTracker.SetFetcher(fetcher)

	// Initialize order manager
	orderMgr := ordermanager.NewManager(registry, db, fetcher)

	// Initialize web server
	server := web.NewServer(cfg.App.Host, cfg.App.Port, orderMgr, fetcher, portfolioTracker)

	// Start background tasks
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	fetcher.Start(ctx)
	portfolioTracker.Start(ctx)
	orderMgr.StartExpirationLoop(ctx)

	// Start web server
	go func() {
		slog.Info("starting web server", "host", cfg.App.Host, "port", cfg.App.Port)
		if err := server.ListenAndServe(); err != nil {
			slog.Error("web server error", "error", err)
		}
	}()

	// Graceful shutdown
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig

	slog.Info("shutting down...")
	cancel()

	// Flush portfolio snapshot
	if err := portfolioTracker.SaveSnapshot(ctx); err != nil {
		slog.Error("failed to save portfolio snapshot", "error", err)
	}

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer shutdownCancel()

	if err := server.Shutdown(shutdownCtx); err != nil {
		slog.Error("server shutdown error", "error", err)
	}

	if err := db.Close(); err != nil {
		slog.Error("database close error", "error", err)
	}

	slog.Info("server stopped")
}

// parseKeyFile reads key.txt and returns a nested map of exchange->key->value
func parseKeyFile(data string) (map[string]map[string]string, error) {
	result := make(map[string]map[string]string)
	return result, nil
}