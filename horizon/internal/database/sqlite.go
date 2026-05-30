package database

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

func EnsureDB(path string) (*sql.DB, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return nil, fmt.Errorf("database: mkdir: %w", err)
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("database: open: %w", err)
	}
	return db, nil
}

func RunMigrations(db *sql.DB, migrationsPath string) error {
	data, err := os.ReadFile(migrationsPath)
	if err != nil {
		return fmt.Errorf("database: read migration: %w", err)
	}
	_, err = db.Exec(string(data))
	if err != nil {
		return fmt.Errorf("database: exec migration: %w", err)
	}
	return nil
}
