"""Send competing requests concurrently. Start services first: python3 run.py."""
import os
from concurrent.futures import ThreadPoolExecutor
from stock_rescue.common import call


if __name__ == '__main__':
    # With two coordinator replicas: COORDINATOR_URLS=http://localhost:8000,http://localhost:8002
    urls = os.getenv('COORDINATOR_URLS', 'http://localhost:8000').split(',')
    jobs = [('store-a', 30), ('store-b', 25)]

    def request(index):
        store, quantity = jobs[index]
        return call(urls[index % len(urls)].rstrip('/') + '/api/allocate', 'POST', {
            'requests': [{'store_id': store, 'lot_id': 'yogurt-01', 'quantity': quantity}]
        })['results'][0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, range(2)))
    for result in results:
        print(f"{result['store_id']}: requested {result['quantity']}, "
              f"allocated {result['allocated']} — {result['reason']}")
    state = call(urls[0].rstrip('/') + '/api/state')
    yogurt = next(lot for lot in state['lots'] if lot['id'] == 'yogurt-01')
    committed = sum(row['quantity'] for row in state['reservations']
                    if row['lot_id'] == yogurt['id'] and row['status'] in ('reserved', 'collected'))
    assert committed <= yogurt['quantity'], 'Over-allocation detected!'
    print(f"Verified: {committed}/{yogurt['quantity']} units committed; "
          f"{yogurt['available']} available.")
