import unittest

from cashflow_validation_report import cashflow_structure
from inflation_linked_bonds import load_inflation_linked_bond_types


class CashflowValidationReportTests(unittest.TestCase):
    def test_known_inflation_linked_isin_overrides_description(self):
        self.assertEqual(cashflow_structure("IT0005713547", "Generic government bond"), "inflation_linked")

    def test_configuration_contains_all_audited_inflation_linked_bonds(self):
        self.assertEqual(len(load_inflation_linked_bond_types()), 17)

    def test_standard_bond_remains_standard(self):
        self.assertEqual(cashflow_structure("TEST", "BTP 01/03/2030 3,5%"), "standard")


if __name__ == "__main__":
    unittest.main()
