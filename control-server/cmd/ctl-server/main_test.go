package main

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestWaitUntilReadinessAndDeadline(t *testing.T) {
	attempts := 0
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := waitUntil(ctx, func(context.Context) error {
		attempts++
		if attempts < 3 {
			return errors.New("starting")
		}
		return nil
	})
	if err != nil || attempts != 3 {
		t.Fatal("did not wait for readiness", err, attempts)
	}
	ended, stop := context.WithCancel(context.Background())
	stop()
	if waitUntil(ended, func(context.Context) error { return errors.New("unavailable") }) == nil {
		t.Fatal("deadline ignored")
	}
}
