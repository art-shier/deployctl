package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/config"
	"github.com/art-shier/deployctl/control-server/internal/httpapi"
	"github.com/art-shier/deployctl/control-server/internal/store"
	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "init" {
		f := flag.NewFlagSet("init", flag.ExitOnError)
		dir := f.String("keys-dir", "/run/ctl-keys", "private keys directory")
		f.Parse(os.Args[2:])
		if err := config.Initialize(*dir); err != nil {
			log.Fatal("key initialization failed; verify private directory permissions")
		}
		log.Print("keys initialized; owner credential is in owner.token")
		return
	}
	c, err := config.Load()
	if err != nil {
		log.Fatal("invalid server configuration or key files: ", err)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	startup, done := context.WithTimeout(ctx, 30*time.Second)
	cfg, err := pgxpool.ParseConfig(c.DatabaseURL)
	if err != nil {
		log.Fatal("invalid database configuration")
	}
	cfg.MaxConns = 20
	pool, err := pgxpool.NewWithConfig(startup, cfg)
	if err != nil {
		log.Fatal("database unavailable")
	}
	defer pool.Close()
	if err = waitUntil(startup, pool.Ping); err != nil {
		log.Fatal("database did not become ready during startup")
	}
	db := store.New(pool, c.Cipher)
	if err = db.Migrate(startup); err != nil {
		log.Fatal("database migration failed")
	}
	done()
	server := &http.Server{Addr: c.Listen, Handler: httpapi.New(db, c.API), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 60 * time.Second, WriteTimeout: 65 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16384}
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Print("HTTP server failed")
			cancel()
		}
	}()
	log.Print("ctl server ready on ", c.Listen)
	<-ctx.Done()
	shutdown, finish := context.WithTimeout(context.Background(), 15*time.Second)
	defer finish()
	server.Shutdown(shutdown)
}

func waitUntil(ctx context.Context, check func(context.Context) error) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := check(ctx); err == nil {
			return nil
		}
		timer := time.NewTimer(200 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}
