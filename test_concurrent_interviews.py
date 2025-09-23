#!/usr/bin/env python3
"""
Test script to verify concurrent interview functionality
"""
import requests
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor

# Configuration
BASE_URL = "http://localhost:5000"
TEST_USERS = [
    "student1@test.com",
    "student2@test.com", 
    "student3@test.com",
    "student4@test.com",
    "student5@test.com"
]

def login_user(email):
    """Login a user and return session data"""
    try:
        # First register the user
        register_data = {
            "name": f"Test User {email}",
            "course_name": "Computer Science",
            "email_id": email,
            "mobile_no": "1234567890",
            "center": "Test Center",
            "batch_no": "TEST2024",
            "password": "testpass123"
        }
        
        response = requests.post(f"{BASE_URL}/register", data=register_data)
        if response.status_code != 200:
            print(f"Registration failed for {email}: {response.status_code}")
            return None
        
        # Then login
        login_data = {
            "username": email,
            "password": "testpass123"
        }
        
        session = requests.Session()
        response = session.post(f"{BASE_URL}/login", data=login_data)
        
        if response.status_code == 200:
            print(f"Login successful for {email}")
            return session
        else:
            print(f"Login failed for {email}: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"Error logging in {email}: {e}")
        return None

def simulate_interview(session, user_email):
    """Simulate an interview session"""
    try:
        print(f"Starting interview simulation for {user_email}")
        
        # Start interview
        response = session.post(f"{BASE_URL}/start_interview")
        if response.status_code != 200:
            print(f"Failed to start interview for {user_email}: {response.status_code}")
            return False
        
        data = response.json()
        if data.get('status') != 'success':
            print(f"Interview start failed for {user_email}: {data.get('message')}")
            return False
        
        print(f"Interview started for {user_email}")
        
        # Simulate answering questions
        for i in range(3):  # Answer 3 questions
            time.sleep(2)  # Simulate thinking time
            
            # Process answer
            answer_data = {
                "answer": f"This is my answer to question {i+1} for {user_email}",
                "is_final": True
            }
            
            response = session.post(f"{BASE_URL}/process_answer", json=answer_data)
            if response.status_code == 200:
                data = response.json()
                print(f"Question {i+1} answered for {user_email}: {data.get('status')}")
                
                if data.get('interview_complete'):
                    print(f"Interview completed for {user_email}")
                    break
            else:
                print(f"Failed to process answer for {user_email}: {response.status_code}")
        
        return True
        
    except Exception as e:
        print(f"Error in interview simulation for {user_email}: {e}")
        return False

def test_concurrent_interviews():
    """Test concurrent interviews"""
    print("Testing concurrent interview functionality...")
    
    # Login all users
    sessions = {}
    for email in TEST_USERS:
        session = login_user(email)
        if session:
            sessions[email] = session
        time.sleep(1)  # Small delay between logins
    
    if not sessions:
        print("No users could be logged in. Test failed.")
        return False
    
    print(f"Successfully logged in {len(sessions)} users")
    
    # Run interviews concurrently
    with ThreadPoolExecutor(max_workers=len(sessions)) as executor:
        futures = []
        for email, session in sessions.items():
            future = executor.submit(simulate_interview, session, email)
            futures.append(future)
        
        # Wait for all interviews to complete
        results = []
        for future in futures:
            result = future.result()
            results.append(result)
    
    successful_interviews = sum(results)
    print(f"Completed {successful_interviews}/{len(sessions)} interviews successfully")
    
    # Check monitoring dashboard
    try:
        # Login as admin to check monitoring
        admin_session = requests.Session()
        admin_data = {"username": "admin", "password": "admin123"}
        response = admin_session.post(f"{BASE_URL}/login", data=admin_data)
        
        if response.status_code == 200:
            # Get monitoring stats
            response = admin_session.get(f"{BASE_URL}/api/monitoring/stats")
            if response.status_code == 200:
                stats = response.json()
                print("\nMonitoring Statistics:")
                print(f"Active Interviews: {stats['interviews']['active_interviews']}")
                print(f"Total Completed: {stats['interviews']['total_completed']}")
                print(f"System Requests: {stats['system']['total_requests']}")
                print(f"Connection Pool Active: {stats['system']['connection_pool']['active_connections']}")
            else:
                print("Failed to get monitoring stats")
        else:
            print("Failed to login as admin")
            
    except Exception as e:
        print(f"Error checking monitoring: {e}")
    
    return successful_interviews == len(sessions)

if __name__ == "__main__":
    success = test_concurrent_interviews()
    if success:
        print("\n✅ Concurrent interview test PASSED!")
    else:
        print("\n❌ Concurrent interview test FAILED!")
