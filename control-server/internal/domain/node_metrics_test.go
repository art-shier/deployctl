package domain

import (
	"math"
	"testing"
)

func TestNodeMetricsRejectInvalidSamples(t *testing.T) {
	for _, value := range []float64{-1, 101, math.NaN(), math.Inf(1)} {
		if (NodeMetrics{CPUPercent: &value}).Validate() == nil {
			t.Fatal("invalid CPU accepted", value)
		}
	}
	for _, capacity := range []ResourceCapacity{{TotalBytes: 0}, {TotalBytes: 10, AvailableBytes: -1}, {TotalBytes: 10, AvailableBytes: 11}} {
		if (NodeMetrics{Memory: &capacity}).Validate() == nil {
			t.Fatal("invalid memory accepted")
		}
		if (NodeMetrics{Disk: &capacity}).Validate() == nil {
			t.Fatal("invalid disk accepted")
		}
	}
	if (NodeMetrics{}).Validate() != nil {
		t.Fatal("unavailable samples rejected")
	}
}
