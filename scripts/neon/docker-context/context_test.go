package context_test

import (
	"os"
	"strings"
	"testing"

	"github.com/moby/patternmatcher"
	"github.com/moby/patternmatcher/ignorefile"
)

// Use Docker/Moby's parser and parent-aware matcher, not gitignore semantics.
func TestReviewedDockerContext(t *testing.T) {
	file, err := os.Open("../../../deploy/neon/Dockerfile.dockerignore")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	patterns, err := ignorefile.ReadAll(file)
	if err != nil {
		t.Fatal(err)
	}
	matcher, err := patternmatcher.New(patterns)
	if err != nil {
		t.Fatal(err)
	}
	allowed := []string{
		"hosted-api/dist/standalone/standalone.js", "hosted-api/dist/standalone/package.json",
		"hosted-api/dist/release-manifest.json", "deploy/neon/Dockerfile", "deploy/neon/verify-standalone.mjs",
	}
	denied := []string{
		"hosted-api/.env.local", "deploy/neon/private/fixture.key", "hosted-api/src/standalone.ts",
		"hosted-api/dist/function/index.js", "hosted-api/dist/standalone/extra.js",
		"hosted-api/dist/standalone/.snapshot", "hosted-api/dist/standalone/nested/fixture.json",
		"deploy/neon/unreviewed.mjs", ".git/config", ".env", "private/export.json", "fixture.pem",
	}
	for _, group := range []struct {
		paths    []string
		excluded bool
	}{{allowed, false}, {denied, true}} {
		for _, path := range group.paths {
			t.Run(strings.ReplaceAll(path, "/", "_"), func(t *testing.T) {
				excluded, err := matcher.MatchesOrParentMatches(path)
				if err != nil {
					t.Fatal(err)
				}
				if excluded != group.excluded {
					t.Fatalf("context path %q excluded=%v, want %v", path, excluded, group.excluded)
				}
			})
		}
	}
}
