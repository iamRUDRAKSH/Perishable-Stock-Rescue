"""Integration tests use real HTTP, two coordinators, and a temporary database."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from stock_rescue.common import APIError, call, now
from stock_rescue.coordinator import make_server as coordinator_server
from stock_rescue.database import StockDB
from stock_rescue.inventory import make_server as stock_server


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = StockDB(Path(self.temp.name) / 'stock.db')
        self.servers = []
        self.threads = []
        self.stock_url = self.start(stock_server('127.0.0.1', 0, self.db))
        self.urls = [self.start(coordinator_server('127.0.0.1', 0, self.stock_url))
                     for _ in range(2)]

    def start(self, server):
        self.servers.append(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.threads.append(thread)
        return f'http://127.0.0.1:{server.server_port}'

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()
        self.temp.cleanup()

    def allocate(self, requests, replica=0):
        return call(self.urls[replica] + '/api/allocate', 'POST', {'requests': requests})['results']

    @staticmethod
    def request(store='store-a', quantity=30, lot='yogurt-01'):
        return {'store_id': store, 'quantity': quantity, 'lot_id': lot}

    def test_batch_priority_partial_allocation_and_collection(self):
        rows = self.allocate([self.request('store-b', 25), self.request('store-a', 30)])
        self.assertEqual([(row['store_id'], row['allocated']) for row in rows],
                         [('store-a', 30), ('store-b', 10)])
        reservation = rows[0]['reservation']
        for _ in range(2):  # Repeating collection must not consume stock again.
            result = call(self.stock_url + '/collect/' + reservation['id'], 'POST', {})
            self.assertEqual(result['status'], 'collected')
        state = call(self.stock_url + '/stock')
        self.assertEqual(next(lot['available'] for lot in state['lots']
                              if lot['id'] == 'yogurt-01'), 0)

    def test_concurrent_requests_through_two_replicas(self):
        def compete(index):
            return self.allocate([self.request(quantity=7)], index % 2)[0]['allocated']
        with ThreadPoolExecutor(max_workers=12) as pool:
            allocations = list(pool.map(compete, range(12)))
        self.assertEqual(sum(allocations), 40)
        state = call(self.stock_url + '/stock')
        self.assertEqual(sum(row['quantity'] for row in state['reservations']), 40)

    def test_expired_reservation_releases_stock(self):
        reservation = self.allocate([self.request()])[0]['reservation']
        with self.db.connection() as db:
            db.execute('UPDATE reservations SET deadline=? WHERE id=?',
                       (now() - 1, reservation['id']))
        with self.assertRaises(APIError) as caught:
            call(self.stock_url + '/collect/' + reservation['id'], 'POST', {})
        self.assertEqual(caught.exception.status, 409)
        result = self.allocate([self.request(quantity=40)])[0]
        self.assertEqual(result['allocated'], 40)

    def test_overlapping_requests_can_use_new_lot_and_custom_quantities(self):
        self.allocate([self.request(quantity=40)])  # Original yogurt is exhausted.
        lot = call(self.urls[0] + '/api/stock', 'POST', {
            'product': 'Milk', 'quantity': 17, 'expires_in_hours': 12})
        requests = [self.request('store-b', 14, lot['id']),
                    self.request('store-a', 11, lot['id'])]
        barrier = threading.Barrier(2)

        def compete(index):
            barrier.wait(timeout=5)
            return self.allocate([requests[index]])[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, range(2)))
        self.assertEqual(sum(result['allocated'] for result in results), 17)
        for result, request in zip(results, requests):
            self.assertEqual(result['store_id'], request['store_id'])
            self.assertEqual(result['lot_id'], lot['id'])
            self.assertEqual(result['quantity'], request['quantity'])
            self.assertLessEqual(result['allocated'], request['quantity'])
        self.assertEqual(next(row['available'] for row in self.db.snapshot()['lots']
                              if row['id'] == lot['id']), 0)

    def test_infeasible_travel_and_invalid_batch(self):
        result = self.allocate([self.request('store-c', 10, 'bread-01')])[0]
        self.assertEqual(result['allocated'], 0)
        self.assertIn('expiry', result['reason'])
        with self.assertRaises(APIError) as caught:
            self.allocate([self.request(), self.request(quantity=-5)])
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(call(self.stock_url + '/stock')['reservations'], [])

    def test_add_stock_persists_and_can_be_reserved(self):
        before = call(self.stock_url + '/stock')
        started = now()
        lot = call(self.urls[0] + '/api/stock', 'POST', {
            'product': ' Milk ', 'quantity': 30, 'expires_in_hours': 12})
        self.assertEqual(lot['product'], 'Milk')
        self.assertEqual(lot['available'], 30)
        self.assertGreaterEqual(lot['expires_at'], started + 12 * 3600)
        state = call(self.urls[1] + '/api/state')
        self.assertEqual(len(state['lots']), len(before['lots']) + 1)
        self.assertIn(lot, StockDB(self.db.path).snapshot()['lots'])
        second = call(self.urls[0] + '/api/stock', 'POST', {
            'product': 'Milk', 'quantity': 5, 'expires_in_hours': 6})
        self.assertNotEqual(lot['id'], second['id'])
        result = self.allocate([self.request(quantity=10, lot=lot['id'])], replica=1)[0]
        self.assertEqual(result['allocated'], 10)

    def test_invalid_stock_is_rejected_without_inserting(self):
        for changes in [{'product': ' '}, {'product': 12}, {'product': 'x' * 81},
                        {'quantity': 0}, {'quantity': True}, {'quantity': 1.5},
                        {'quantity': 1000001}, {'expires_in_hours': -1},
                        {'expires_in_hours': 8761}, {'expires_in_hours': None}]:
            with self.subTest(changes=changes):
                with self.assertRaises(APIError) as caught:
                    call(self.urls[0] + '/api/stock', 'POST', {
                        'product': 'Milk', 'quantity': 30, 'expires_in_hours': 12,
                        **changes})
                self.assertEqual(caught.exception.status, 400)
        self.assertEqual(len(call(self.stock_url + '/stock')['lots']), 2)

    def test_direct_stock_requests_cannot_over_allocate(self):
        payload = {**self.request(quantity=41), 'travel_minutes': 20}
        with self.assertRaises(APIError) as caught:
            call(self.stock_url + '/reservations', 'POST', payload)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(call(self.stock_url + '/stock')['reservations'], [])

    def test_persistence_and_expired_lot(self):
        self.allocate([self.request(quantity=10)])
        reopened = StockDB(self.db.path)
        self.assertEqual(len(reopened.snapshot()['reservations']), 1)
        with self.db.connection() as db:
            db.execute("UPDATE lots SET expires_at=? WHERE id='bread-01'", (now() - 1,))
        result = self.allocate([self.request(lot='bread-01')])[0]
        self.assertEqual(result['allocated'], 0)


if __name__ == '__main__':
    unittest.main()
