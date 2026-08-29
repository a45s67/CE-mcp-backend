import unittest

from ce_mcp.models import (
    Address,
    ContractViolation,
    Session,
    SessionState,
    require_expected_generation,
)


class AddressTests(unittest.TestCase):
    def test_serializes_64_bit_address_as_canonical_string(self) -> None:
        address = Address(
            address="0x00007ff612341234",
            expression="sample.exe+0x1234",
            module="sample.exe",
            rva="0x1234",
            pointer_width=64,
        )
        value = address.to_dict()
        self.assertEqual(value["address"], "0x00007FF612341234")
        self.assertIsInstance(value["address"], str)
        self.assertEqual(value["rva"], "0x1234")

    def test_rejects_numeric_and_out_of_width_addresses(self) -> None:
        with self.assertRaises((ContractViolation, AttributeError)):
            Address(address=1234)  # type: ignore[arg-type]
        with self.assertRaises(ContractViolation):
            Address(address="0x100000000", pointer_width=32)


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Session(
            session_id="ce-01jabcdef",
            generation=7,
            state=SessionState.PAUSED,
            pid=4242,
            architecture="x86_64",
            pointer_width=64,
        )

    def test_matching_generation_allows_mutation(self) -> None:
        self.assertIsNone(require_expected_generation(self.session, 7))

    def test_stale_generation_returns_non_retryable_error(self) -> None:
        error = require_expected_generation(self.session, 6)
        assert error is not None
        self.assertEqual(error.code, "STALE_SESSION")
        self.assertFalse(error.safe_to_retry)
        self.assertEqual(error.details["actualGeneration"], 7)  # type: ignore[index]

    def test_architecture_and_pointer_width_must_match(self) -> None:
        with self.assertRaises(ContractViolation):
            Session("ce-01jabcdef", 1, SessionState.RUNNING, 1, "x86", 64)


if __name__ == "__main__":
    unittest.main()
