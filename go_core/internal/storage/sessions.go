package storage

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"sync"
	"time"
)

type Session struct {
	ID       string
	Conn     *Conn
	LastUsed time.Time
}

type SessionManager struct {
	mu       sync.Mutex
	path     string
	sessions map[string]*Session
}

func NewSessionManager(path string) *SessionManager {
	return &SessionManager{path: path, sessions: map[string]*Session{}}
}

func sessionID() string {
	var buf [16]byte
	_, _ = rand.Read(buf[:])
	return hex.EncodeToString(buf[:])
}

func (m *SessionManager) Open() (*Session, error) {
	conn, err := Open(m.path)
	if err != nil {
		return nil, err
	}
	s := &Session{ID: sessionID(), Conn: conn, LastUsed: time.Now()}
	m.mu.Lock()
	m.sessions[s.ID] = s
	m.mu.Unlock()
	return s, nil
}

func (m *SessionManager) Get(id string) (*Session, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := m.sessions[id]
	if s == nil {
		return nil, errors.New("database session not found")
	}
	s.LastUsed = time.Now()
	return s, nil
}

func (m *SessionManager) Close(id string) error {
	m.mu.Lock()
	s := m.sessions[id]
	delete(m.sessions, id)
	m.mu.Unlock()
	if s == nil {
		return nil
	}
	return s.Conn.Close()
}

func (m *SessionManager) Reap(maxIdle time.Duration) int {
	cutoff := time.Now().Add(-maxIdle)
	var expired []*Session
	m.mu.Lock()
	for id, s := range m.sessions {
		if s.LastUsed.Before(cutoff) {
			expired = append(expired, s)
			delete(m.sessions, id)
		}
	}
	m.mu.Unlock()
	for _, s := range expired {
		_ = s.Conn.Close()
	}
	return len(expired)
}

func (m *SessionManager) CloseAll() {
	m.mu.Lock()
	all := make([]*Session, 0, len(m.sessions))
	for _, s := range m.sessions {
		all = append(all, s)
	}
	m.sessions = map[string]*Session{}
	m.mu.Unlock()
	for _, s := range all {
		_ = s.Conn.Close()
	}
}
