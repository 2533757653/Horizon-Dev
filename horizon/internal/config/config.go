package config

import (
	"gopkg.in/yaml.v3"
	"os"
)

type Config struct {
	App       AppConfig       `yaml:"app"`
	Exchanges ExchangesConfig `yaml:"exchanges"`
	Trading   TradingConfig   `yaml:"trading"`
	LLM       LLMConfig       `yaml:"llm"`
	Database  DatabaseConfig  `yaml:"database"`
}

type AppConfig struct {
	Host     string `yaml:"host"`
	Port     int    `yaml:"port"`
	LogLevel string `yaml:"log_level"`
}

type ExchangesConfig struct {
	Binance     ExchangeConfig `yaml:"binance"`
	HTX         ExchangeConfig `yaml:"htx"`
	Hyperliquid ExchangeConfig `yaml:"hyperliquid"`
	Bitget      ExchangeConfig `yaml:"bitget"`
}

type ExchangeConfig struct {
	Enabled    bool   `yaml:"enabled"`
	RecvWindow int    `yaml:"recv_window"`
	APIKey     string `yaml:"api_key"`
	Secret     string `yaml:"secret"`
	WalletAddr string `yaml:"wallet_address"`
	PrivateKey string `yaml:"private_key"`
}

type TradingConfig struct {
	OrderApprovalTimeout   int `yaml:"order_approval_timeout"`
	MarketDataPollInterval int `yaml:"market_data_poll_interval"`
	StrategyPollInterval   int `yaml:"strategy_poll_interval"`
}

type LLMConfig struct {
	Enabled bool   `yaml:"enabled"`
	APIKey  string `yaml:"api_key"`
	Model   string `yaml:"model"`
}

type DatabaseConfig struct {
	Path string `yaml:"database"`
}

func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	return &cfg, nil
}
