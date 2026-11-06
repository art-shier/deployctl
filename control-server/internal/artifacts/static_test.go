package artifacts

import (
	"archive/zip"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestImplicitStaticDirectoriesAreBounded(t *testing.T) {
	path := filepath.Join(t.TempDir(), "many.zip")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	z := zip.NewWriter(f)
	for i := 0; i < 5001; i++ {
		entry, err := z.Create(fmt.Sprintf("item-%d/a/b/c/d/e/f/g/h/file", i))
		if err != nil {
			t.Fatal(err)
		}
		entry.Write([]byte("one"))
	}
	z.Close()
	f.Close()
	if _, err = ValidateStaticFile(path, ""); err == nil {
		t.Fatal("implicit directory expansion accepted")
	}
}

func TestSharedStaticArchiveFixtures(t *testing.T) {
	root := filepath.Join("..", "..", "..", "tests", "fixtures", "static-archives")
	raw, err := os.ReadFile(filepath.Join(root, "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name  string
		Valid bool
	}
	if json.Unmarshal(raw, &cases) != nil {
		t.Fatal("fixtures")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			r, err := ValidateStaticFile(filepath.Join(root, c.Name), "")
			if c.Valid {
				if err != nil || r.ExpandedSize != 9 || r.EntryCount != 3 || r.Image != "" {
					t.Fatal(r, err)
				}
			} else if err == nil {
				t.Fatal("unsafe archive accepted")
			}
		})
	}
}

func TestStaticArtifactStorageIsImmutable(t *testing.T) {
	src := filepath.Join("..", "..", "..", "tests", "fixtures", "static-archives", "valid.zip")
	r, err := ValidateStaticFile(src, "")
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	if err = PublishTempFile(root, src, r); err != nil {
		t.Fatal(err)
	}
	target, err := ArtifactPath(root, r)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(target)
	original, _ := os.ReadFile(src)
	if string(raw) != string(original) {
		t.Fatal("stored different bytes")
	}
	os.WriteFile(target, []byte("tampered"), 0600)
	if PublishTempFile(root, src, r) == nil {
		t.Fatal("overwrote tampered artifact")
	}
}
