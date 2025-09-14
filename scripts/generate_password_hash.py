#!/usr/bin/env python3
"""
Generate correct password hashes for the authentication system.
"""

import hashlib

def hash_password(password: str) -> str:
    """Hash password with the same method as AuthManager."""
    salt = "kick_monitor_salt"
    return hashlib.sha256((password + salt).encode()).hexdigest()

if __name__ == "__main__":
    # Generate hashes for default users
    admin_hash = hash_password("admin123")
    user_hash = hash_password("user123")
    
    print("Password hashes for user_schema.sql:")
    print(f"admin123 -> {admin_hash}")
    print(f"user123  -> {user_hash}")