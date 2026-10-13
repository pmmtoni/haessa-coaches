"""Render isolated test records, never the application database. Pass an output HTML path."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_bom_stock as suite

if __name__ == '__main__':
    test = suite.StockTests()
    test.setUp()
    try:
        test.post(stock_status='In Stock', component='Existing stock')
        test.post(component='Awaiting delivery')
        test.post(component='Historical receipt', actual_delivery_date='2026-09-01',
                  expected_delivery_date='2026-08-01')
        page = test.client.get('/coaches/1/bom')
        assert page.status_code == 200
        Path(sys.argv[1]).write_bytes(page.data)
    finally:
        test.tearDown()
