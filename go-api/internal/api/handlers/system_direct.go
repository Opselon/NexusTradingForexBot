// Package handlers implements direct Go serving for system capabilities and version metadata.
//
// These endpoints serve static/stateless platform metadata directly from Go without
// proxying through the Python runtime, while providing seamless fallback to Python
// when needed or attached.
package handlers

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/routing"
)

const (
	// DefaultAPIVersion is the canonical platform API version.
	DefaultAPIVersion = "v1"
	// DefaultSpec is the relative path to the API contract spec.
	DefaultSpec = "docs/api/API_PLATFORM_V1.md"
	// DefaultEndpointCount is the total mounted endpoint count in the platform specification.
	DefaultEndpointCount = 182
	// DefaultProductName is the internal product name.
	DefaultProductName = "NexusScalpEngine"
	// DefaultProductDisplay is the human-facing product title.
	DefaultProductDisplay = "Nexus Trading Forex Bot"
	// DefaultVersion is the release version fallback.
	DefaultVersion = "9.0.14"
	// DefaultChannel is the release distribution channel.
	DefaultChannel = "production"

	// CommitStatusRelease marks a recorded release commit.
	CommitStatusRelease = "RELEASE_COMMIT"
	// CommitStatusNotRecorded marks an unrecorded/unavailable commit.
	CommitStatusNotRecorded = "NOT_RECORDED"

	// CommitSourceRepo indicates the commit was read directly from the git repository.
	CommitSourceRepo = "repository"
	// CommitSourceBinary indicates the version info was read from binary defaults.
	CommitSourceBinary = "binary"
	// CommitSourcePython indicates the version info was resolved via the Python runtime.
	CommitSourcePython = "python"
)

// CanonicalDomains lists the 19 platform domains and their operational status (1 = active).
var CanonicalDomains = map[string]int{
	"audit":         1,
	"config":        1,
	"database":      1,
	"decisions":     1,
	"execution":     1,
	"features":      1,
	"incidents":     1,
	"indicators":    1,
	"market":        1,
	"marketplace":   1,
	"model":         1,
	"observability": 1,
	"positions":     1,
	"research":      1,
	"risk":          1,
	"runtime":       1,
	"shadow":        1,
	"signals":       1,
	"system":        1,
}

// PaginationCapability defines the pagination parameters in the capabilities envelope.
type PaginationCapability struct {
	Model         string `json:"model"`
	PageParam     string `json:"page_param"`
	PageSizeParam string `json:"page_size_param"`
	MaxPageSize   int    `json:"max_page_size"`
}

// DefaultPagination is the standard pagination contract across v1 endpoints.
var DefaultPagination = PaginationCapability{
	Model:         "page",
	PageParam:     "page",
	PageSizeParam: "page_size",
	MaxPageSize:   200,
}

// CapabilitiesData represents the payload of GET /api/v1/system/capabilities.
type CapabilitiesData struct {
	APIVersion    string               `json:"api_version"`
	Spec          string               `json:"spec"`
	ReadOnly      bool                 `json:"read_only"`
	Domains       map[string]int       `json:"domains"`
	DomainCount   int                  `json:"domain_count"`
	EndpointCount int                  `json:"endpoint_count"`
	Pagination    PaginationCapability `json:"pagination"`
	GeneratedAt   string               `json:"generated_at"`
}

// VersionData represents the payload of GET /api/v1/system/version.
type VersionData struct {
	Product        string `json:"product"`
	ProductDisplay string `json:"product_display"`
	Version        string `json:"version"`
	Commit         string `json:"commit"`
	CommitSource   string `json:"commit_source"`
	CommitStatus   string `json:"commit_status"`
	Channel        string `json:"channel"`
	GeneratedAt    string `json:"generated_at"`
}

// SystemDirectHandler serves system capabilities and version metadata directly in Go.
type SystemDirectHandler struct {
	py *python.Client

	mu                 sync.RWMutex
	cachedCapabilities *CapabilitiesData
	cachedVersion      *VersionData

	forceFallback bool
	gitResolver   func() (string, error)
}

// Type aliases for flexible instantiation.
type SystemDirect = SystemDirectHandler
type SystemDirectHandlers = SystemDirectHandler

// commitOnce + commitCache guarantee ONE git resolution per PROCESS. The
// legacy VersionDirect path rebuilt the handler on every request, which reset
// cachedVersion to nil each call — so every request re-ran `git rev-parse
// HEAD` (~43ms on Windows) and the "direct" route ended up slower than the
// Python proxy it replaced. Caching the resolved commit process-wide makes
// any handler built later (including per-request rebuilds) reuse it.
var (
	commitOnce     sync.Once
	commitCache    string
	commitErr      error
	commitResolved bool
)

// resolveCommitOnce runs the (expensive, exec-based) git resolution exactly
// once per process, then returns the cached result.
func resolveCommitOnce() (string, error) {
	commitOnce.Do(func() {
		commitCache, commitErr = resolveGitCommit()
		commitResolved = true
	})
	return commitCache, commitErr
}

// NewSystemDirect constructs a direct handler with an optional Python client.
//
// The handler caches VersionData on first use, and the git commit it embeds
// is cached process-wide (resolveCommitOnce), so the exec happens at most
// once per process no matter how many handlers are constructed.
func NewSystemDirect(py *python.Client) *SystemDirectHandler {
	return &SystemDirectHandler{
		py:          py,
		gitResolver: resolveCommitOnce,
	}
}

// ResetCache clears cached metadata (useful in tests or hot reloads).
func (h *SystemDirectHandler) ResetCache() {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.cachedCapabilities = nil
	h.cachedVersion = nil
}

// SetForceFallback forces the handler to use the Python runtime.
func (h *SystemDirectHandler) SetForceFallback(force bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.forceFallback = force
}

// SetGitCommitResolver overrides the git commit resolution func (useful in tests).
//
// The override replaces the process-wide cached resolver for THIS handler:
// resolution still runs at most ONCE (the default is once per process, an
// override is once per handler), instead of on every request. Without that
// guarantee a rebuilt or reset handler would re-run resolution per request —
// exactly the Wave-6 regression this package guards against.
func (h *SystemDirectHandler) SetGitCommitResolver(resolver func() (string, error)) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if resolver == nil {
		h.gitResolver = resolveCommitOnce
		return
	}
	var sha string
	var err error
	var once sync.Once
	h.gitResolver = func() (string, error) {
		once.Do(func() { sha, err = resolver() })
		return sha, err
	}
}

// Capabilities serves GET /api/v1/system/capabilities directly from Go.
func (h *SystemDirectHandler) Capabilities(w http.ResponseWriter, r *http.Request) {
	if isFallbackRequested(r) || h.forceFallback || !routing.DirectServingEnabled() {
		h.fallbackCapabilities(w, r)
		return
	}

	directFn := func(bw http.ResponseWriter, req *http.Request) error {
		data := h.getCapabilitiesData(req.Context())
		if data == nil {
			return errors.New("capabilities data unavailable")
		}
		respond.OK(bw, req, data)
		return nil
	}
	fallbackFn := func(fw http.ResponseWriter, req *http.Request) {
		h.fallbackCapabilities(fw, req)
	}

	routing.ServeWithFallback(w, r, directFn, fallbackFn)
}

// Version serves GET /api/v1/system/version directly from Go.
func (h *SystemDirectHandler) Version(w http.ResponseWriter, r *http.Request) {
	if isFallbackRequested(r) || h.forceFallback || !routing.DirectServingEnabled() {
		h.fallbackVersion(w, r)
		return
	}

	directFn := func(bw http.ResponseWriter, req *http.Request) error {
		data, err := h.getVersionData(req.Context())
		if err != nil {
			return err
		}
		respond.OK(bw, req, data)
		return nil
	}
	fallbackFn := func(fw http.ResponseWriter, req *http.Request) {
		h.fallbackVersion(fw, req)
	}

	routing.ServeWithFallback(w, r, directFn, fallbackFn)
}

// CapabilitiesDirect provides direct Go serving on the legacy SystemHandlers receiver.
// It reuses the ONE long-lived SystemDirectHandler built in NewSystem so the
// cached capabilities survive across requests.
func (h *SystemHandlers) CapabilitiesDirect(w http.ResponseWriter, r *http.Request) {
	h.direct.Capabilities(w, r)
}

// VersionDirect provides direct Go serving on the legacy SystemHandlers receiver.
// It reuses the ONE long-lived SystemDirectHandler built in NewSystem so the
// cached VersionData survives across requests (see SystemHandlers.direct).
func (h *SystemHandlers) VersionDirect(w http.ResponseWriter, r *http.Request) {
	h.direct.Version(w, r)
}

// SystemCapabilitiesDirectHandler returns a standalone http.HandlerFunc.
func SystemCapabilitiesDirectHandler(py *python.Client) http.HandlerFunc {
	return NewSystemDirect(py).Capabilities
}

// SystemVersionDirectHandler returns a standalone http.HandlerFunc.
func SystemVersionDirectHandler(py *python.Client) http.HandlerFunc {
	return NewSystemDirect(py).Version
}

func (h *SystemDirectHandler) getCapabilitiesData(_ context.Context) *CapabilitiesData {
	h.mu.RLock()
	if h.cachedCapabilities != nil {
		defer h.mu.RUnlock()
		return h.cachedCapabilities
	}
	h.mu.RUnlock()

	h.mu.Lock()
	defer h.mu.Unlock()
	if h.cachedCapabilities != nil {
		return h.cachedCapabilities
	}

	domains := make(map[string]int, len(CanonicalDomains))
	for k, v := range CanonicalDomains {
		domains[k] = v
	}

	h.cachedCapabilities = &CapabilitiesData{
		APIVersion:    DefaultAPIVersion,
		Spec:          DefaultSpec,
		ReadOnly:      true,
		Domains:       domains,
		DomainCount:   len(domains),
		EndpointCount: DefaultEndpointCount,
		Pagination:    DefaultPagination,
		GeneratedAt:   respond.UTCNowISO(),
	}
	return h.cachedCapabilities
}

func (h *SystemDirectHandler) getVersionData(_ context.Context) (*VersionData, error) {
	h.mu.RLock()
	if h.cachedVersion != nil {
		defer h.mu.RUnlock()
		return h.cachedVersion, nil
	}
	h.mu.RUnlock()

	h.mu.Lock()
	defer h.mu.Unlock()
	if h.cachedVersion != nil {
		return h.cachedVersion, nil
	}

	resolver := h.gitResolver
	if resolver == nil {
		resolver = resolveGitCommit
	}

	commit, err := resolver()
	if err != nil || commit == "" {
		return nil, fmt.Errorf("git commit unavailable: %w", err)
	}

	h.cachedVersion = &VersionData{
		Product:        DefaultProductName,
		ProductDisplay: DefaultProductDisplay,
		Version:        resolveVersion(),
		Commit:         commit,
		CommitSource:   CommitSourceRepo,
		CommitStatus:   CommitStatusRelease,
		Channel:        DefaultChannel,
		GeneratedAt:    respond.UTCNowISO(),
	}
	return h.cachedVersion, nil
}

func (h *SystemDirectHandler) fallbackCapabilities(w http.ResponseWriter, r *http.Request) {
	if h.py != nil && h.py.Configured() {
		var env pyEnvelope[map[string]any]
		err := h.py.DoJSON(r.Context(), http.MethodGet, "/api/v1/system/capabilities", nil, &env)
		if err == nil && env.Data != nil {
			respond.OK(w, r, env.Data)
			return
		}
		if be, ok := python.AsBoundary(err); ok {
			respond.ReplayBoundary(w, r, be)
			return
		}
	}

	// Safe fallback to direct capabilities if Python is absent or fails.
	data := h.getCapabilitiesData(r.Context())
	if data != nil {
		respond.OK(w, r, data)
		return
	}
	respond.FailDependencyUnavailable(w, r, "capabilities unavailable")
}

func (h *SystemDirectHandler) fallbackVersion(w http.ResponseWriter, r *http.Request) {
	h.mu.RLock()
	if h.cachedVersion != nil {
		defer h.mu.RUnlock()
		respond.OK(w, r, h.cachedVersion)
		return
	}
	h.mu.RUnlock()

	var verData *VersionData
	if h.py != nil && h.py.Configured() {
		var env pyEnvelope[map[string]any]
		err := h.py.GetJSON(r.Context(), "/api/v1/system/version", &env)
		if err == nil && env.Data != nil {
			getString := func(k, dflt string) string {
				if v, ok := env.Data[k].(string); ok && v != "" {
					return v
				}
				return dflt
			}
			verData = &VersionData{
				Product:        getString("product", DefaultProductName),
				ProductDisplay: getString("product_display", DefaultProductDisplay),
				Version:        getString("version", DefaultVersion),
				Commit:         getString("commit", "unknown"),
				CommitSource:   getString("commit_source", CommitSourcePython),
				CommitStatus:   getString("commit_status", CommitStatusRelease),
				Channel:        getString("channel", DefaultChannel),
				GeneratedAt:    respond.UTCNowISO(),
			}
		}
	}

	if verData == nil {
		verData = &VersionData{
			Product:        DefaultProductName,
			ProductDisplay: DefaultProductDisplay,
			Version:        resolveVersion(),
			Commit:         "unknown",
			CommitSource:   CommitSourceBinary,
			CommitStatus:   CommitStatusNotRecorded,
			Channel:        DefaultChannel,
			GeneratedAt:    respond.UTCNowISO(),
		}
	}

	h.mu.Lock()
	if h.cachedVersion == nil {
		h.cachedVersion = verData
	}
	h.mu.Unlock()

	respond.OK(w, r, verData)
}

func isFallbackRequested(r *http.Request) bool {
	if r == nil {
		return false
	}
	if strings.EqualFold(r.Header.Get("X-NSE-Fallback"), "true") || r.Header.Get("X-NSE-Fallback") == "1" {
		return true
	}
	if r.URL != nil && (r.URL.Query().Get("fallback") == "1" || strings.EqualFold(r.URL.Query().Get("fallback"), "true")) {
		return true
	}
	return false
}

var versionRegex = regexp.MustCompile(`(?m)^version\s*=\s*["']([^"']+)["']`)

func resolveVersion() string {
	repoRoot := findRepoRoot()
	var candidates []string
	if repoRoot != "" {
		candidates = append(candidates, filepath.Join(repoRoot, "pyproject.toml"))
	}
	candidates = append(candidates,
		"pyproject.toml",
		"../pyproject.toml",
		"../../pyproject.toml",
	)

	for _, p := range candidates {
		if data, err := os.ReadFile(p); err == nil {
			m := versionRegex.FindSubmatch(data)
			if len(m) > 1 {
				return string(m[1])
			}
		}
	}
	return DefaultVersion
}

func findRepoRoot() string {
	if cwd, err := os.Getwd(); err == nil {
		dir := cwd
		for i := 0; i < 8; i++ {
			if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
				return dir
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		for i := 0; i < 8; i++ {
			if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
				return dir
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	return ""
}

func resolveGitCommit() (string, error) {
	repoRoot := findRepoRoot()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "git", "rev-parse", "HEAD")
	if repoRoot != "" {
		cmd.Dir = repoRoot
	}
	out, err := cmd.Output()
	if err == nil {
		sha := strings.TrimSpace(string(out))
		if isHexSHA(sha) {
			return sha, nil
		}
	}

	if repoRoot != "" {
		sha, err := readGitHEADFromDir(repoRoot)
		if err == nil && isHexSHA(sha) {
			return sha, nil
		}
	}

	return "", errors.New("git commit resolution failed")
}

func readGitHEADFromDir(baseDir string) (string, error) {
	gitPath := filepath.Join(baseDir, ".git")
	fi, err := os.Stat(gitPath)
	if err != nil {
		return "", err
	}

	var gitDir string
	if fi.IsDir() {
		gitDir = gitPath
	} else {
		content, err := os.ReadFile(gitPath)
		if err != nil {
			return "", err
		}
		lines := strings.Split(string(content), "\n")
		for _, line := range lines {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "gitdir:") {
				p := strings.TrimSpace(strings.TrimPrefix(line, "gitdir:"))
				if !filepath.IsAbs(p) {
					p = filepath.Join(baseDir, p)
				}
				gitDir = filepath.Clean(p)
				break
			}
		}
	}
	if gitDir == "" {
		return "", errors.New("could not determine gitdir")
	}

	headFile := filepath.Join(gitDir, "HEAD")
	headContent, err := os.ReadFile(headFile)
	if err != nil {
		return "", err
	}
	headStr := strings.TrimSpace(string(headContent))
	if isHexSHA(headStr) {
		return headStr, nil
	}

	if strings.HasPrefix(headStr, "ref:") {
		refPath := strings.TrimSpace(strings.TrimPrefix(headStr, "ref:"))
		refFile := filepath.Join(gitDir, refPath)
		if refBytes, err := os.ReadFile(refFile); err == nil {
			sha := strings.TrimSpace(string(refBytes))
			if isHexSHA(sha) {
				return sha, nil
			}
		}

		commondirFile := filepath.Join(gitDir, "commondir")
		if cdBytes, err := os.ReadFile(commondirFile); err == nil {
			commonDir := strings.TrimSpace(string(cdBytes))
			if !filepath.IsAbs(commonDir) {
				commonDir = filepath.Join(gitDir, commonDir)
			}
			commonDir = filepath.Clean(commonDir)
			refFile = filepath.Join(commonDir, refPath)
			if refBytes, err := os.ReadFile(refFile); err == nil {
				sha := strings.TrimSpace(string(refBytes))
				if isHexSHA(sha) {
					return sha, nil
				}
			}
			if sha, err := findInPackedRefs(filepath.Join(commonDir, "packed-refs"), refPath); err == nil {
				return sha, nil
			}
		}

		if sha, err := findInPackedRefs(filepath.Join(gitDir, "packed-refs"), refPath); err == nil {
			return sha, nil
		}
	}

	return "", errors.New("unable to resolve ref to commit SHA")
}

func findInPackedRefs(packedPath, refName string) (string, error) {
	data, err := os.ReadFile(packedPath)
	if err != nil {
		return "", err
	}
	lines := strings.Split(string(data), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "#") || strings.HasPrefix(line, "^") || line == "" {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) >= 2 && parts[1] == refName {
			if isHexSHA(parts[0]) {
				return parts[0], nil
			}
		}
	}
	return "", errors.New("ref not found in packed-refs")
}

func isHexSHA(s string) bool {
	if len(s) != 40 {
		return false
	}
	for _, c := range s {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return false
		}
	}
	return true
}
