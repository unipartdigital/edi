"""EDI local filesystem connection tests"""

from contextlib import contextmanager
from unittest.mock import patch
from . import test_edi_gateway


class TestEdiConnectionLocal(test_edi_gateway.EdiGatewayFileSystemCase):
    """EDI local filesystem connection tests"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        IrModel = cls.env['ir.model']
        cls.gateway.write({
            'name': "Test local filesystem gateway",
            'model_id': IrModel._get_id('edi.connection.local'),
        })
        cls.path_receive.path = "receive"
        cls.path_send.path = "send"

    @contextmanager
    def patch_paths(self, path_files):
        """Patch EDI paths to include specified test files

        The ``edi.connection.local.connect()`` method is mocked to use
        a temporary local directory.
        """
        EdiConnectionLocal = self.env['edi.connection.local']
        with super().patch_paths(path_files) as ctx:
            with patch.object(EdiConnectionLocal.__class__, 'connect',
                              autospec=True, return_value=ctx.temppath):
                yield ctx
