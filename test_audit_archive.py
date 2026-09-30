import hashlib
import io
import json
import unittest
import zipfile
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from audit_archive_core import (ArchiveError, boundaries, month_bounds, make_archive,
                                archive_then_cleanup)
from audit_drive import DriveStore
from audit_archive import Repository, run, direct_database_url


def row(id=1, at=None, details='Updated supplier date'):
    return {'id': id, 'coach_id': 99, 'coach_number': '99', 'action': 'bom_item_updated',
            'changed_by': 'Tester', 'details': details,
            'created_at': at or datetime(2025, 8, 15, 10, 0)}


class RetentionTests(unittest.TestCase):
    def test_calendar_year_not_365_days(self):
        completed, cutoff = boundaries(datetime(2026, 9, 24, 10, tzinfo=timezone.utc))
        self.assertEqual(completed, datetime(2026, 8, 31, 22))
        self.assertEqual(cutoff, datetime(2025, 9, 24, 10))

    def test_leap_day(self):
        _, cutoff = boundaries(datetime(2024, 2, 29, 10, tzinfo=timezone.utc))
        self.assertEqual(cutoff, datetime(2023, 2, 28, 10))

    def test_local_month_boundary_and_year_rollover(self):
        completed, cutoff = boundaries(datetime(2025, 12, 31, 22, tzinfo=timezone.utc))
        self.assertEqual(completed, datetime(2025, 12, 31, 22))
        self.assertEqual(cutoff, datetime(2024, 12, 31, 22))
        self.assertEqual(month_bounds('2025-12'), (datetime(2025,11,30,22), datetime(2025,12,31,22)))

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError): boundaries(datetime(2026,9,1))


class ArchiveTests(unittest.TestCase):
    def test_reproducible_zip_and_lossless_source(self):
        rows=[row(details='=SUM(1,2)\n"quoted", café'), row(id=2, details=None)]
        first=make_archive('2025-08',rows)
        self.assertEqual(first, make_archive('2025-08', rows))
        name, data, digest=first
        self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifest=json.loads(z.read('manifest.json'))
            source=[json.loads(x) for x in z.read('audit.jsonl').splitlines()]
            self.assertEqual(source[0]['details'],rows[0]['details'])
            self.assertIsNone(source[1]['details'])
            self.assertEqual(manifest['row_count'],2)
            self.assertEqual(manifest['csv_sha256'],hashlib.sha256(z.read('audit.csv')).hexdigest())
            self.assertIn(b"'=SUM",z.read('audit.csv'))
            self.assertEqual(manifest['jsonl_sha256'],hashlib.sha256(z.read('audit.jsonl')).hexdigest())

    def test_bad_archive_inputs(self):
        for rows in [[],[row(2),row(1)],[row(1),row(1)],[row(at=datetime(2025,9,15))]]:
            with self.subTest(rows=rows):
                with self.assertRaises(ArchiveError): make_archive('2025-08',rows)

    def test_only_exact_verified_old_ids_deleted(self):
        cutoff=datetime(2025,8,20)
        rows=[row(),row(2,cutoff),row(3,datetime(2025,8,21))]
        calls=[]
        store=Mock()
        store.put_verified.side_effect=lambda *a: calls.append('verified') or 'file1'
        repo=Mock()
        repo.delete_exact.side_effect=lambda ids,c: calls.append(('delete',ids,c)) or len(ids)
        result=archive_then_cleanup(store,repo,'2025-08',rows,cutoff,True)
        self.assertEqual(calls,['verified',('delete',[1],cutoff)])
        self.assertEqual(result['deleted'],1)

    def test_upload_or_verify_failure_never_deletes(self):
        store,repo=Mock(),Mock()
        store.put_verified.side_effect=ArchiveError('Upload or verification failed')
        with self.assertRaises(ArchiveError):
            archive_then_cleanup(store,repo,'2025-08',[row()],datetime(2026,1,1),True)
        repo.delete_exact.assert_not_called()

    def test_archive_only_never_deletes(self):
        store,repo=Mock(),Mock()
        store.put_verified.return_value='file1'
        result=archive_then_cleanup(store,repo,'2025-08',[row()],datetime(2026,1,1),False)
        self.assertEqual(result['deleted'],0)
        repo.delete_exact.assert_not_called()

    def test_wrong_delete_count_raises_for_transaction_rollback(self):
        store,repo=Mock(),Mock()
        store.put_verified.return_value='file1'
        repo.delete_exact.return_value=0
        with self.assertRaises(ArchiveError):
            archive_then_cleanup(store,repo,'2025-08',[row()],datetime(2026,1,1),True)

    def test_no_old_rows_no_delete(self):
        store,repo=Mock(),Mock()
        store.put_verified.return_value='file1'
        archive_then_cleanup(store,repo,'2025-08',[row()],datetime(2025,1,1),True)
        repo.delete_exact.assert_not_called()


class DriveVerificationTests(unittest.TestCase):
    def setUp(self):
        self.store=DriveStore('test','test','test','folder1')
        self.data=b'archive-content'
        self.digest=hashlib.sha256(self.data).hexdigest()
        self.meta={'id':'file1','parents':['folder1'],'size':str(len(self.data)), 'trashed':False}

    def test_retry_reuses_and_downloads_existing_file(self):
        self.store._request=Mock(side_effect=[{'files':[self.meta]},self.meta,self.data])
        self.assertEqual(self.store.put_verified('x.zip',self.data,self.digest),'file1')
        self.assertIn('alt=media',self.store._request.call_args_list[-1].args[0])
        self.assertTrue(all(len(c.args)==1 for c in self.store._request.call_args_list))

    def test_new_file_readback(self):
        self.store._request=Mock(side_effect=[{'files':[]},self.meta,self.meta,self.data])
        self.assertEqual(self.store.put_verified('x.zip',self.data,self.digest),'file1')
        self.assertEqual(self.store._request.call_args_list[1].args[1],'POST')

    def test_corrupt_download_stops(self):
        self.store._request=Mock(side_effect=[{'files':[self.meta]},self.meta,b'corrupt-content'])
        with self.assertRaises(ArchiveError): self.store.put_verified('x.zip',self.data,self.digest)

    def test_wrong_folder_size_or_trashed_stops(self):
        for patch_value in [{'parents':['other']},{'size':'1'},{'trashed':True}]:
            self.store._request=Mock(side_effect=[{'files':[self.meta]},dict(self.meta,**patch_value)])
            with self.assertRaises(ArchiveError): self.store.put_verified('x.zip',self.data,self.digest)


class DatabaseBoundaryTests(unittest.TestCase):
    def test_pooler_url_rejected_before_connecting(self):
        with self.assertRaises(ArchiveError): direct_database_url('postgresql://test:example@ep-demo-pooler.example/neondb')
        self.assertEqual(direct_database_url('postgresql+psycopg://test:example@ep-demo.example/neondb'),
                         'postgresql://test:example@ep-demo.example/neondb')

    def test_unknown_schema_stops(self):
        connection=Mock()
        connection.execute.return_value.fetchall.return_value=[{'column_name':'id','data_type':'integer'}]
        with self.assertRaises(ArchiveError): Repository(connection).verify_schema()

    def test_deletion_is_limited_to_ids_and_cutoff(self):
        connection=Mock()
        connection.execute.return_value.rowcount=2
        cutoff=datetime(2025,1,1)
        self.assertEqual(Repository(connection).delete_exact([1,2],cutoff),2)
        sql,params=connection.execute.call_args.args
        self.assertIn('public.coach_audit',sql)
        self.assertIn('id = ANY(%s) AND created_at < %s',sql)
        self.assertEqual(params,([1,2],cutoff))

    def test_dry_run_does_not_contact_drive(self):
        repo=Mock()
        repo.server_now.return_value=datetime(2026,9,24,tzinfo=timezone.utc)
        repo.counts.return_value={'total':100,'completed_month_records':99,'older_than_12_months':12,'missing_timestamps':0}
        store=Mock()
        with patch('audit_archive.Repository',return_value=repo),patch('builtins.print'):
            run(Mock(),store,'dry-run')
        store.check_folder.assert_not_called()
        repo.months.assert_not_called()

    def test_failed_batch_rolls_back_context_and_stops(self):
        connection=Mock()
        from unittest.mock import MagicMock
        tx=MagicMock()
        connection.transaction.return_value=tx
        repo=Mock()
        repo.server_now.return_value=datetime(2026,9,24,tzinfo=timezone.utc)
        repo.counts.return_value={'total':1,'missing_timestamps':0}
        repo.months.return_value=['2025-08']
        repo.batch.return_value=[row()]
        store=Mock()
        store.put_verified.side_effect=ArchiveError('offline')
        with patch('audit_archive.Repository',return_value=repo),patch('builtins.print'):
            with self.assertRaises(ArchiveError): run(connection,store,'run')
        repo.delete_exact.assert_not_called()
        self.assertIs(tx.__exit__.call_args.args[0],ArchiveError)

    def test_batch_part_size_reduction(self):
        conn=Mock()
        conn.execute.return_value.fetchall.return_value=[row(i) for i in range(1,5)]
        with patch('audit_archive.PART_LIMIT', 2),patch('audit_archive.make_archive',side_effect=lambda month,rows: ('name', b'x'*len(rows), 'hash')):
            self.assertEqual(len(Repository(conn).batch('2025-08',0)),2)


if __name__ == '__main__': unittest.main()
