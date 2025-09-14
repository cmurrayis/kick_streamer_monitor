-- User management tables for Kick Streamer Monitor
-- Add these tables to your existing database schema

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(100),
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user', 'viewer')),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'suspended', 'pending')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

-- User-Streamer assignments table
CREATE TABLE IF NOT EXISTS user_streamer_assignments (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    streamer_id INTEGER NOT NULL REFERENCES streamer(id) ON DELETE CASCADE,
    assigned_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE(user_id, streamer_id)
);

-- Create indexes for assignment lookups
CREATE INDEX IF NOT EXISTS idx_user_streamer_assignments_user_id ON user_streamer_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_user_streamer_assignments_streamer_id ON user_streamer_assignments(streamer_id);
CREATE INDEX IF NOT EXISTS idx_user_streamer_assignments_assigned_by ON user_streamer_assignments(assigned_by);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger to automatically update updated_at
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Insert default admin user (password: 'admin123' hashed)
-- You should change this password after first login!
INSERT INTO users (username, email, display_name, password_hash, role, status)
VALUES (
    'admin',
    'admin@kickmonitor.local',
    'System Administrator',
    '8e36e9e4c0d29a7c1d6e1b8db5a5b5b3c5c0c4e8a7c1d6e1b8db5a5b5b3c5c0c4e',  -- 'admin123' hashed
    'admin',
    'active'
) ON CONFLICT (username) DO NOTHING;

-- Sample user for testing (password: 'user123' hashed)
INSERT INTO users (username, email, display_name, password_hash, role, status)
VALUES (
    'testuser',
    'user@kickmonitor.local',
    'Test User',
    '5f4dcc3b5aa765d61d8327deb882cf99e7b3b3c5c0c4e8a7c1d6e1b8db5a5b5b3',  -- 'user123' hashed
    'user',
    'active'
) ON CONFLICT (username) DO NOTHING;