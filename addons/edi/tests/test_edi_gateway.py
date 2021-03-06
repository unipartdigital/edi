"""EDI gateway tests"""

from collections import namedtuple
from contextlib import contextmanager
import pathlib
import shutil
import tempfile
from odoo import fields
from .common import EdiCase


def skipUnlessCanInitiate(f):
    """Skip test case unless gateway is capable of initiating connections"""
    def wrapper(self, *args, **kwargs):
        # pylint: disable=missing-docstring
        if self.gateway.can_initiate:
            f(self, *args, **kwargs)
        else:
            self.skipTest("Gateway cannot initiate connections")
    return wrapper


def skipUnlessCanReceive(f):
    """Skip test case unless gateway is capable of receiving"""
    def wrapper(self, *args, **kwargs):
        # pylint: disable=missing-docstring
        if self.gateway.path_ids.filtered(lambda x: x.allow_receive):
            f(self, *args, **kwargs)
        else:
            self.skipTest("Gateway has no receive paths")
    return wrapper


def skipUnlessCanSend(f):
    """Skip test case unless gateway is capable of sending"""
    def wrapper(self, *args, **kwargs):
        # pylint: disable=missing-docstring
        if self.gateway.path_ids.filtered(lambda x: x.allow_send):
            f(self, *args, **kwargs)
        else:
            self.skipTest("Gateway has no send paths")
    return wrapper


class EdiGatewayCase(EdiCase):
    """Base test case for EDI gateways"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        IrModel = cls.env['ir.model']
        EdiGateway = cls.env['edi.gateway']
        EdiPath = cls.env['edi.gateway.path']

        # Create gateway
        cls.gateway = EdiGateway.create({
            'name': "Test gateway",
            'model_id': IrModel._get_id('edi.connection.model'),
        })

        # Create paths
        cls.path_receive = EdiPath.create({
            'name': "Test receive path",
            'gateway_id': cls.gateway.id,
            'path': '/',
            'allow_receive': True,
            'allow_send': False,
        })
        cls.path_send = EdiPath.create({
            'name': "Test send path",
            'gateway_id': cls.gateway.id,
            'path': '/',
            'allow_receive': False,
            'allow_send': True,
            'doc_type_ids': [(6, 0, cls.doc_type_unknown.ids)],
        })

    def tearDown(self):
        super().tearDown()
        # Check for exceptions that have been caught and converted to issues
        self.assertEqual(len(self.gateway.issue_ids), 0)

    @contextmanager
    def patch_paths(self, _path_files):
        """Patch EDI paths to include specified test files

        This is a context manager; the patch will be removed when the
        context exits.
        """
        yield

    def assertSent(self, _ctx, _path_files):
        """Assert that specified test files were sent"""
        pass

    @skipUnlessCanInitiate
    def test01_action_test(self):
        """Test the ability to test the connection"""
        old_messages = self.gateway.message_ids
        self.gateway.action_test()
        new_messages = self.gateway.message_ids - old_messages
        self.assertEqual(len(new_messages), 1)

    @skipUnlessCanInitiate
    def test02_transfer_no_paths(self):
        """Test transfer (with no paths defined)"""
        self.gateway.path_ids.unlink()
        old_transfers = self.gateway.transfer_ids
        transfer = self.gateway.do_transfer()
        new_transfers = self.gateway.transfer_ids - old_transfers
        self.assertIn(transfer, new_transfers)
        self.assertEqual(len(new_transfers), 1)

    @skipUnlessCanInitiate
    def test03_action_transfer_no_paths(self):
        """Test transfer action (with no paths defined)"""
        self.gateway.path_ids.unlink()
        self.assertTrue(self.gateway.action_transfer())

    @skipUnlessCanInitiate
    @skipUnlessCanReceive
    def test04_transfer_receive(self):
        """Test receiving attachments"""
        with self.patch_paths({self.path_receive: ['hello_world.txt']}):
            transfer = self.gateway.with_context({
                'default_allow_process': False,
            }).do_transfer()
            self.assertEqual(len(transfer.input_ids), 1)
            self.assertEqual(len(transfer.output_ids), 0)
            self.assertAttachment(transfer.input_ids, 'hello_world.txt')
        with self.patch_paths({self.path_receive: ['hello_world.txt']}):
            transfer = self.gateway.with_context({
                'default_allow_process': False,
            }).do_transfer()
            self.assertEqual(len(transfer.input_ids), 0)
            self.assertEqual(len(transfer.output_ids), 0)

    @skipUnlessCanInitiate
    @skipUnlessCanSend
    def test05_transfer_send(self):
        """Test sending attachments"""
        EdiDocument = self.env['edi.document']
        today = fields.Datetime.now()
        doc = EdiDocument.create({
            'name': "ToDo list",
            'doc_type_id': self.doc_type_unknown.id,
            'state': 'done',
            'prepare_date': today,
            'execute_date': today,
        })
        attachment = self.create_output_attachment(doc, 'hello_world.txt')
        with self.patch_paths({}) as ctx:
            transfer = self.gateway.do_transfer()
            self.assertEqual(len(transfer.input_ids), 0)
            self.assertEqual(len(transfer.output_ids), 1)
            self.assertIn(attachment, transfer.output_ids)
            self.assertSent(ctx, {self.path_send: ['hello_world.txt']})
        with self.patch_paths({self.path_send: ['hello_world.txt']}) as ctx:
            transfer = self.gateway.do_transfer()
            self.assertEqual(len(transfer.input_ids), 0)
            self.assertEqual(len(transfer.output_ids), 0)
            self.assertSent(ctx, {})


class EdiGatewayFileSystemCase(EdiGatewayCase):
    """Base test case for filesystem-like EDI gateways"""

    Context = namedtuple('EdiGatewayFileSystemCaseContext',
                         ['temppath', 'subpaths', 'path_files'])

    @property
    def path_subdirs(self):
        """Mapping from EDI paths to temporary subdirectories

        By default, the ``edi.gateway.path.path`` attribute is used as
        the subdirectory name.
        """
        return {path: path.path for path in self.gateway.path_ids}

    @contextmanager
    def patch_paths(self, path_files):
        """Patch EDI paths to include specified test files

        Create a temporary directory containing subdirectories for
        each defined path on the EDI gateway, and populate these
        subdirectories with the specified test files.

        This is a context manager; the temporary directory will be
        deleted when the context exits.
        """

        # Duplicate original EDI path -> files mapping for later comparison
        path_files = {path: list(files) for path, files in path_files.items()}

        # Create and populate temporary directory
        with tempfile.TemporaryDirectory() as tempdir:

            # Create subdirectory for each defined EDI path
            temppath = pathlib.Path(tempdir)
            subpaths = {path: temppath.joinpath(subdir)
                        for path, subdir in self.path_subdirs.items()}
            for subpath in subpaths.values():
                subpath.mkdir(parents=True, exist_ok=True)

            # Copy in specified test files
            for path, files in path_files.items():
                for file in files:
                    src = self.files.joinpath(file)
                    dst = subpaths[path].joinpath(file)
                    self.assertFalse(dst.exists())
                    shutil.copy(src, dst)

            yield self.Context(pathlib.Path(temppath), subpaths, path_files)

    def assertSent(self, ctx, path_files):
        """Assert that specified test files were sent

        The contents of the temporary directory will be compared
        against the expected contents.
        """
        expected = {
            path.name: set((file, self.files.joinpath(file).read_bytes())
                           for file in (set(ctx.path_files.get(path, ())) |
                                        set(path_files.get(path, ()))))
            for path in ctx.subpaths
        }
        actual = {
            path.name: set((file.name, file.read_bytes())
                           for file in subpath.iterdir())
            for path, subpath in ctx.subpaths.items()
        }
        self.assertEqual(actual, expected)
