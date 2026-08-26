package main

import (
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"xianxia/core/internal/server"
)

func main() {
	address := os.Getenv("ENGINE_ADDR")
	if address == "" {
		address = os.Getenv("CORE_ADDR")
	}
	if address == "" {
		address = ":8081"
	}
	databasePath := os.Getenv("DATABASE_PATH")
	if databasePath == "" {
		databasePath = "/data/xianxia.sqlite3"
	}

	worldPath := os.Getenv("WORLD_DATA_PATH")
	if worldPath == "" {
		worldPath = "/app/content/world.json"
	}
	engine, err := server.New(databasePath, worldPath)
	if err != nil {
		log.Fatalf("open authoritative sqlite: %v", err)
	}
	defer engine.Close()
	stop := make(chan struct{})
	engine.StartReaper(stop)

	httpServer := &http.Server{
		Addr: address, Handler: engine.Handler(),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second,
		WriteTimeout: 60 * time.Second, IdleTimeout: 60 * time.Second,
	}
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM)
	go func() { <-signals; close(stop); _ = httpServer.Close() }()
	log.Printf("xianxia authoritative Go engine listening on %s (sqlite=%s, WAL enabled)", address, databasePath)
	if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
