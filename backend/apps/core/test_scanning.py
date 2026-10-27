"""The virus scanner client, tested against a small stand-in that speaks clamd's real INSTREAM protocol.

A real ClamAV daemon is not used here: it needs its signature database, which is not available to this test run.
"""
import socket
import struct
import threading

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError

from . import scanning


class FakeClamd:
    """Reads zINSTREAM, collects the chunks, answers like clamd (EICAR is 'FOUND')."""

    def __init__(self, answer=None, drop=False):
        self.answer, self.drop, self.received, self.command = answer, drop, b"", b""
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def read(self, conn, n):
        data = b""
        while len(data) < n:
            part = conn.recv(n - len(data))
            if not part:
                raise EOFError
            data += part
        return data

    def serve(self):
        conn, _ = self.server.accept()
        with conn:
            self.command = self.read(conn, 10)
            while True:
                size = struct.unpack("!I", self.read(conn, 4))[0]
                if size == 0:
                    break
                self.received += self.read(conn, size)
            if self.drop:
                return
            if self.answer is not None:
                reply = self.answer
            else:
                reply = b"stream: Eicar-Test-Signature FOUND" if b"EICAR-STANDARD" in self.received else b"stream: OK"
            conn.sendall(reply + b"\0")

    def close(self):
        self.server.close()


def settings_for(port):
    return override_settings(UPLOAD_SCANNING="clamd", CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=port, CLAMAV_TIMEOUT=3)


class ScanningTests(SimpleTestCase):
    def test_a_clean_file_is_streamed_whole_and_passes(self):
        fake = FakeClamd()
        data = b"%PDF-1.4 " + b"x" * 200_000  # more than one chunk
        with settings_for(fake.port):
            scanning.scan(data)
        fake.thread.join(2)
        self.assertEqual((fake.command, fake.received), (b"zINSTREAM\0", data))

    def test_an_infected_file_is_rejected_with_a_clear_message(self):
        fake = FakeClamd()
        with settings_for(fake.port), self.assertRaises(ValidationError) as caught:
            scanning.scan(b"X5O!P%@AP EICAR-STANDARD-ANTIVIRUS-TEST-FILE!", "letter")
        self.assertIn("virus", str(caught.exception.detail["file"]))

    def test_a_scanner_that_cannot_be_reached_refuses_the_upload(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]
        with settings_for(dead), self.assertRaises(scanning.ScannerUnavailable):
            scanning.scan(b"anything")

    def test_a_scanner_that_hangs_up_or_answers_oddly_refuses_the_upload(self):
        for fake in (FakeClamd(drop=True), FakeClamd(answer=b"stream: size limit exceeded ERROR"), FakeClamd(answer=b"")):
            with settings_for(fake.port), self.assertRaises(scanning.ScannerUnavailable):
                scanning.scan(b"anything")

    def test_unavailable_is_a_503_not_a_server_crash(self):
        self.assertEqual(scanning.ScannerUnavailable.status_code, 503)

    def test_switched_off_nothing_is_checked(self):
        with override_settings(UPLOAD_SCANNING="off", CLAMAV_PORT=1):
            scanning.scan(b"anything")

    def test_an_uploaded_file_is_left_readable_from_the_start(self):
        fake = FakeClamd()
        upload = SimpleUploadedFile("a.pdf", b"%PDF-1.4 hello")
        with settings_for(fake.port):
            scanning.scan_upload(upload)
        self.assertEqual(upload.read(), b"%PDF-1.4 hello")


import gzip  # noqa: E402
import os  # noqa: E402
import zlib  # noqa: E402
from unittest import skipUnless  # noqa: E402

CLAMAV = os.environ.get("HRMS_TEST_CLAMAV", "")  # host:port of a real clamd, for example 127.0.0.1:3310
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"  # the harmless standard test file


@skipUnless(CLAMAV, "set HRMS_TEST_CLAMAV=host:port to run these against a real ClamAV")
class RealClamavTests(SimpleTestCase):
    def real(self):
        host, port = CLAMAV.rsplit(":", 1)
        return override_settings(UPLOAD_SCANNING="clamd", CLAMAV_HOST=host, CLAMAV_PORT=int(port), CLAMAV_TIMEOUT=30)

    def pdf_carrying_the_test_file(self):
        stream = zlib.compress(EICAR)
        return (
            b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
            + stream + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
        )

    def test_clean_files_pass_including_one_larger_than_a_chunk(self):
        with self.real():
            scanning.scan(b"%PDF-1.4 an ordinary file")
            scanning.scan(b"\x89PNG\r\n\x1a\n" + os.urandom(3_000_000))

    def test_the_standard_test_file_is_found_plain_and_inside_a_pdf_or_archive(self):
        with self.real():
            for name, data in (("plain", EICAR), ("in a pdf", self.pdf_carrying_the_test_file()), ("gzipped", gzip.compress(EICAR))):
                with self.subTest(name), self.assertRaises(ValidationError):
                    scanning.scan(data)
