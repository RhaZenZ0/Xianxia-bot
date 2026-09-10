package main

import (
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"xianxia/core/internal/backupcrypt"
	"xianxia/core/internal/server"
)

func main() {
	// `xianxia-engine decrypt-backup <in.sqlite3.enc> <out.sqlite3>` opens a
	// sealed backup with XIANXIA_BACKUP_KEY and exits (v0.32.0). It is how an
	// off-box copy is read without the engine, and how update.sh's rollback
	// turns the pre-update backup back into a database.
	if len(os.Args) > 1 && os.Args[1] == "decrypt-backup" {
		if len(os.Args) != 4 {
			log.Fatal("usage: xianxia-engine decrypt-backup <in.sqlite3.enc> <out.sqlite3>")
		}
		if err := backupcrypt.DecryptFile(os.Args[2], os.Args[3], os.Getenv("XIANXIA_BACKUP_KEY")); err != nil {
			log.Fatal(err)
		}
		log.Printf("decrypted %s -> %s", os.Args[2], os.Args[3])
		return
	}
	address := os.Getenv("ENGINE_ADDR")
	if address == "" {
		address = os.Getenv("CORE_ADDR")
	}
	if address == "" {
		address = "127.0.0.1:8081"
	}
	databasePath := os.Getenv("DATABASE_PATH")
	if databasePath == "" {
		databasePath = "/data/xianxia.sqlite3"
	}

	worldPath := os.Getenv("WORLD_DATA_PATH")
	if worldPath == "" {
		worldPath = "/app/content/world.json"
	}
	if len(os.Getenv("ENGINE_AUTH_TOKEN")) < 20 {
		log.Fatal("ENGINE_AUTH_TOKEN must be set to at least 20 characters")
	}
	engine, err := server.New(databasePath, worldPath)
	if err != nil {
		log.Fatalf("open authoritative sqlite: %v", err)
	}
	stop := make(chan struct{})
	engine.StartReaper(stop)

	httpServer := &http.Server{
		Addr: address, Handler: engine.Handler(),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second,
		WriteTimeout: 60 * time.Second, IdleTimeout: 60 * time.Second,
	}
	// Drain rather than sever. Shutdown stops accepting connections and waits
	// for the requests already running; only when that finishes (or the grace
	// period runs out) does storage close, so a committed transaction always
	// gets its response out.
	grace := server.ShutdownGraceFromEnv()
	drained := make(chan struct{})
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		signal := <-signals
		log.Printf("received %s; draining in-flight requests (up to %s)", signal, grace)
		if err := server.Drain(httpServer, grace); err != nil {
			// The grace expired with work still running. This is the one path
			// that can still cut a response, so it is said out loud.
			log.Printf("shutdown grace of %s expired with requests still running (%v)", grace, err)
		} else {
			log.Printf("in-flight requests finished; shutting down")
		}
		close(stop)
		close(drained)
	}()
	log.Printf("xianxia authoritative Go engine listening on %s (sqlite=%s, WAL enabled)", address, databasePath)
	err = httpServer.ListenAndServe()
	if err != nil && err != http.ErrServerClosed {
		// A listen failure is not a shutdown: nothing is draining, so close
		// storage here rather than waiting for a signal that will not come.
		engine.Close()
		log.Fatal(err)
	}
	// ListenAndServe returns as soon as Shutdown is *called*, so wait for the
	// drain itself before closing the database out from under it.
	<-drained
	engine.Close()
	log.Print("xianxia authoritative Go engine stopped")
}
