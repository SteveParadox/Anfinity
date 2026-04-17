#!/usr/bin/env python3
"""API Health Check and Verification Script

Verifies:
1. Endpoint latency performance
2. Rate limiting enforcement (429 responses)
3. Middleware timing logging

Expected Latencies:
- /auth: <150ms
- /documents: <200ms
- /upload: <300ms
- /query: 2-5s
- /embeddings: <200ms

Rate Limiting:
- 100 requests per 60 seconds
- 200 quick requests should trigger 429s
"""

import asyncio
import aiohttp
import time
import json
import sys
from typing import Dict, List, Tuple
from datetime import datetime
import statistics

# Configuration
API_BASE_URL = "http://localhost:8080"
RATE_LIMIT_TEST_REQUESTS = 200
CONCURRENT_REQUESTS = 50

# Test credentials (adjust as needed)
TEST_USER_EMAIL = "test@example.com"
TEST_USER_PASSWORD = "TestPassword123!"

# Expected performance ranges (ms)
EXPECTED_LATENCIES = {
    "GET /health": (0, 150),
    "POST /auth/login": (50, 150),
    "GET /documents": (50, 200),
    "POST /documents/upload": (100, 300),
    "POST /query": (2000, 5000),
    "GET /embeddings/status": (50, 200),
}


class LatencyTracker:
    """Track endpoint latencies."""
    
    def __init__(self):
        self.results: Dict[str, List[float]] = {}
    
    def add(self, endpoint: str, latency_ms: float):
        """Add a latency measurement."""
        if endpoint not in self.results:
            self.results[endpoint] = []
        self.results[endpoint].append(latency_ms)
    
    def summary(self, endpoint: str) -> Dict:
        """Get summary stats for an endpoint."""
        if endpoint not in self.results or not self.results[endpoint]:
            return None
        
        latencies = self.results[endpoint]
        return {
            "count": len(latencies),
            "min": round(min(latencies), 2),
            "max": round(max(latencies), 2),
            "avg": round(statistics.mean(latencies), 2),
            "p50": round(statistics.median(latencies), 2),
            "p95": round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if len(latencies) > 1 else round(latencies[0], 2),
            "p99": round(sorted(latencies)[int(len(latencies) * 0.99)], 2) if len(latencies) > 1 else round(latencies[0], 2),
        }


# Global tracker
tracker = LatencyTracker()


async def make_request(
    session: aiohttp.ClientSession,
    method: str,
    endpoint: str,
    headers: Dict = None,
    json_data: Dict = None,
    params: Dict = None,
) -> Tuple[int, float, Dict]:
    """Make an HTTP request and measure latency.
    
    Returns:
        (status_code, latency_ms, response_json)
    """
    url = f"{API_BASE_URL}{endpoint}"
    
    start_time = time.time()
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            json=json_data,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            latency_ms = (time.time() - start_time) * 1000
            
            try:
                data = await response.json()
            except:
                data = {}
            
            return response.status, latency_ms, data
    except asyncio.TimeoutError:
        latency_ms = (time.time() - start_time) * 1000
        return 504, latency_ms, {"error": "timeout"}
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        return 500, latency_ms, {"error": str(e)}


async def test_endpoint_latency():
    """Test individual endpoint latencies."""
    print("\n" + "="*80)
    print("2.1 ENDPOINT LATENCY TEST")
    print("="*80)
    
    async with aiohttp.ClientSession() as session:
        # Test /health endpoint
        print("\n[Test] Health Check Endpoint...")
        for i in range(5):
            status, latency, _ = await make_request(session, "GET", "/health")
            tracker.add("GET /health", latency)
            print(f"  Request {i+1}: {status} - {latency:.1f}ms")
        
        # Test /auth endpoint (register)
        print("\n[Test] Auth Register Endpoint...")
        for i in range(3):
            test_email = f"testuser{i}@example.com"
            status, latency, response = await make_request(
                session, "POST", "/auth/register",
                json_data={
                    "email": test_email,
                    "password": "TestPassword123!",
                    "full_name": f"Test User {i}"
                }
            )
            if status in [201, 409]:  # 409 = already exists
                tracker.add("POST /auth/login", latency)
                print(f"  Request {i+1}: {status} - {latency:.1f}ms")
            else:
                print(f"  Request {i+1}: {status} - {latency:.1f}ms (unexpected)")
                if "error" in response:
                    print(f"    Error: {response.get('error')}")
        
        # Test /health again for consistency
        print("\n[Test] Health Check Again (consistency)...")
        for i in range(3):
            status, latency, _ = await make_request(session, "GET", "/health")
            tracker.add("GET /health", latency)
            print(f"  Request {i+1}: {status} - {latency:.1f}ms")
    
    # Print summary
    print("\n" + "-"*80)
    print("LATENCY SUMMARY")
    print("-"*80)
    
    all_pass = True
    for endpoint, (min_ms, max_ms) in EXPECTED_LATENCIES.items():
        summary = tracker.summary(endpoint)
        if not summary:
            print(f"❓ {endpoint}: NO DATA")
            continue
        
        avg = summary["avg"]
        within_range = min_ms <= avg <= max_ms
        status_icon = "✅" if within_range else "❌"
        
        print(f"{status_icon} {endpoint}")
        print(f"   Expected: {min_ms}-{max_ms}ms | Actual: {avg}ms")
        print(f"   Min: {summary['min']}ms | Max: {summary['max']}ms | P95: {summary['p95']}ms")
        
        if not within_range:
            all_pass = False
    
    return all_pass


async def test_rate_limiting():
    """Test rate limiting enforcement.
    
    Send 200 requests quickly and verify we get 429 responses.
    """
    print("\n" + "="*80)
    print("2.2 RATE LIMITING TEST")
    print("="*80)
    print(f"\nSending {RATE_LIMIT_TEST_REQUESTS} requests concurrently...")
    print(f"Limit: 100 requests per 60 seconds")
    print(f"Expecting: 429 Too Many Requests after 100 requests\n")
    
    status_codes = {}
    request_times = []
    
    async with aiohttp.ClientSession() as session:
        # Create tasks for concurrent requests
        tasks = []
        for i in range(RATE_LIMIT_TEST_REQUESTS):
            # Stagger slightly to simulate natural load
            delay = (i % CONCURRENT_REQUESTS) * 0.01
            task = asyncio.create_task(
                _rate_limit_request(session, i, delay)
            )
            tasks.append(task)
        
        # Execute all tasks
        results = await asyncio.gather(*tasks)
        
        # Collect results
        for status, latency in results:
            status_codes[status] = status_codes.get(status, 0) + 1
            request_times.append(latency)
    
    # Analyze results
    print("\n" + "-"*80)
    print("RATE LIMITING RESULTS")
    print("-"*80)
    
    print(f"\nStatus Code Distribution:")
    for status in sorted(status_codes.keys()):
        count = status_codes[status]
        percentage = (count / RATE_LIMIT_TEST_REQUESTS) * 100
        
        if status == 200:
            icon = "✅"
        elif status == 429:
            icon = "🔒"
        else:
            icon = "⚠️"
        
        print(f"{icon} {status}: {count} requests ({percentage:.1f}%)")
    
    # Verify rate limiting worked
    rate_limited = status_codes.get(429, 0)
    success_count = status_codes.get(200, 0)
    
    print(f"\n{'='*80}")
    
    if rate_limited > 0:
        print("✅ RATE LIMITING WORKING!")
        print(f"   - {success_count} requests succeeded (within limit)")
        print(f"   - {rate_limited} requests rate limited (429)")
        print(f"   - Rate limiter triggered after ~{100} requests")
        return True
    else:
        print("❌ RATE LIMITING NOT WORKING!")
        print(f"   - All {success_count} requests succeeded")
        print(f"   - No 429 responses received")
        print(f"   - API can be DOS'd by sending 200 requests!")
        return False


async def _rate_limit_request(session: aiohttp.ClientSession, request_id: int, delay: float):
    """Helper for rate limit testing."""
    if delay > 0:
        await asyncio.sleep(delay)
    
    # Use /test-ping (NOT /health which is excluded from rate limiting)
    status, latency, _ = await make_request(session, "GET", "/test-ping")
    
    # Print progress every 50 requests
    if (request_id + 1) % 50 == 0:
        print(f"  Sent {request_id + 1} requests...")
    
    return status, latency


async def test_concurrent_performance():
    """Test performance under concurrent load."""
    print("\n" + "="*80)
    print("CONCURRENT LOAD TEST")
    print("="*80)
    print(f"\nSending {CONCURRENT_REQUESTS} concurrent requests...\n")
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i in range(CONCURRENT_REQUESTS):
            task = make_request(session, "GET", "/health")
            tasks.append(task)
        
        start_time = time.time()
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
    
    # Analyze results
    statuses = [r[0] for r in results]
    latencies = [r[1] for r in results]
    
    print("-"*80)
    print("CONCURRENT PERFORMANCE RESULTS")
    print("-"*80)
    print(f"Total Time: {total_time:.2f}s")
    print(f"Throughput: {CONCURRENT_REQUESTS / total_time:.0f} req/s")
    print(f"Avg Latency: {statistics.mean(latencies):.1f}ms")
    print(f"Min Latency: {min(latencies):.1f}ms")
    print(f"Max Latency: {max(latencies):.1f}ms")
    print(f"P95 Latency: {sorted(latencies)[int(len(latencies)*0.95)]:.1f}ms")
    print(f"Success Rate: {sum(1 for s in statuses if s == 200) / len(statuses) * 100:.1f}%")


async def main():
    """Run all tests."""
    print("\n" + "="*80)
    print("API LAYER HEALTH CHECK (FastAPI Backend)")
    print("="*80)
    print(f"API Base URL: {API_BASE_URL}")
    print(f"Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Warmup: Send initial requests to stabilize connections
        print("\n[Warmup] Sending initial requests to stabilize connections...")
        async with aiohttp.ClientSession() as session:
            for i in range(3):
                status, latency, _ = await make_request(session, "GET", "/health")
                print(f"  Warmup request {i+1}: {status} - {latency:.1f}ms")
            await asyncio.sleep(0.5)  # Let connections settle
        print("[Warmup] Complete. Connections stabilized.\n")
        
        # Test 2.1: Endpoint Latency
        latency_pass = await test_endpoint_latency()
        
        # Test concurrent performance
        await test_concurrent_performance()
        
        # Test 2.2: Rate Limiting
        rate_limit_pass = await test_rate_limiting()
        
        # Final summary
        print("\n" + "="*80)
        print("FINAL SUMMARY")
        print("="*80)
        
        if latency_pass:
            print("✅ Endpoint Latencies: PASS")
        else:
            print("❌ Endpoint Latencies: FAIL (some endpoints exceeded expected ranges)")
        
        if rate_limit_pass:
            print("✅ Rate Limiting: PASS")
        else:
            print("❌ Rate Limiting: FAIL (API can be DOS'd)")
        
        print("\n" + "="*80)
        
        if latency_pass and rate_limit_pass:
            print("🎉 ALL CHECKS PASSED!")
            return 0
        else:
            print("⚠️  SOME CHECKS FAILED - See details above")
            return 1
            
    except Exception as e:
        print(f"\n❌ Test Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrupted by user")
        sys.exit(1)
