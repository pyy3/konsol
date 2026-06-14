"""Demo Control — DO NOT MERGE TO MAIN.

============================================================================
  DEMO-ONLY. Not for production. Must never be merged to main.
  Lives on branch demo/error-injection-DO-NOT-MERGE.
  Guarded by site_config flag `enable_demo_errors` so it is inert unless a
  site explicitly opts in (set via: bench --site <site> set-config enable_demo_errors 1).
============================================================================

A one-checkbox toggle in Frappe Desk (EPM > Demo Control) that injects/removes
deliberately-broken "accountant mistake" journals in epm_raw, so the close
assertion suite flips red -> fix -> green for demos.
"""
import frappe
from frappe.model.document import Document
from konsol.clickhouse import execute

_NOW = "2026-06-14 12:00:00.000"

# (journal_no, entity, ccy, [(account, amount, is_credit), ...]) — each maps to an assert_*
_MISTAKES = [
    ("ERR-UNBAL-001", "USMF", "USD", [("6010", 100000, "No"), ("1010", 90000, "Yes")]),
    ("ERR-BADACCT-001", "USMF", "USD", [("9999", 5000, "No"), ("1010", 5000, "Yes")]),
    ("ERR-ICONE-001", "USMF", "USD", [("1300", 75000, "No"), ("4030", 75000, "Yes")]),
]


def _enabled():
    return bool(frappe.conf.get("enable_demo_errors"))


def _dim(acct):
    return '[{"MAINACCOUNT":"%s","COSTCENTER":"","DEPARTMENT":"","BUSINESSUNIT":""}]' % acct


def _inject():
    hv = []
    lv = []
    h = 999000000
    l = 999500000
    for jn, ent, ccy, legs in _MISTAKES:
        h += 1
        hv.append("('err-h-%d','%s',%d,'2024-06-15','%s',2024,6,'%s','Current','DemoError')"
                  % (h, _NOW, h, jn, ent))
        for a, amt, cr in legs:
            l += 1
            lv.append("('err-l-%d','%s',%d,%d,'%s-000','%s',%d,%d,'%s','Ledger','DEMO_ERROR','%s','2024-06-15')"
                      % (l, _NOW, l, h, a, _dim(a), amt, amt, ccy, cr))
    execute("INSERT INTO epm_raw.GeneralJournalEntryBiEntities "
            "(_airbyte_raw_id,_airbyte_extracted_at,SourceKey,AccountingDate,JournalNumber,"
            "FiscalCalendarYear,FiscalCalendarPeriod,SubledgerVoucherDataAreaId,PostingLayer,JournalCategory) "
            "VALUES " + ",".join(hv))
    execute("INSERT INTO epm_raw.GeneralJournalAccountEntryBiEntities "
            "(_airbyte_raw_id,_airbyte_extracted_at,SourceKey,GeneralJournalEntry,LedgerAccount,"
            "LedgerDimensionValuesJson,AccountingCurrencyAmount,TransactionCurrencyAmount,"
            "TransactionCurrencyCode,PostingType,Text,IsCredit,AccountingDate) "
            "VALUES " + ",".join(lv))


def _remove():
    execute("DELETE FROM epm_raw.GeneralJournalAccountEntryBiEntities WHERE Text='DEMO_ERROR'")
    execute("DELETE FROM epm_raw.GeneralJournalEntryBiEntities WHERE JournalCategory='DemoError'")


class DemoControl(Document):

    def on_update(self):
        if not self.has_value_changed("inject_errors"):
            return
        if not _enabled():
            frappe.throw(
                "Demo error injection is disabled. Enable it for this site with: "
                "<code>bench --site &lt;site&gt; set-config enable_demo_errors 1</code>"
            )
        if self.inject_errors:
            _inject()
            self.db_set("last_action", f"Injected {len(_MISTAKES)} demo error journals at {frappe.utils.now()}")
        else:
            _remove()
            self.db_set("last_action", f"Removed demo error journals at {frappe.utils.now()}")
        if self.rebuild_on_toggle:
            frappe.enqueue("konsol.epm.doctype.demo_control.demo_control.rebuild_assertions",
                           queue="default", timeout=600)
        frappe.msgprint(self.last_action + (" — rebuild enqueued." if self.rebuild_on_toggle else ""))


def rebuild_assertions():
    """Scoped dbt build so the close assertions re-evaluate (demo)."""
    import subprocess
    path = frappe.get_single("EPM Settings").dbt_project_path or "/home/frappe/dbt_project"
    subprocess.run(
        ["/home/frappe/frappe-bench/env/bin/dbt", "build",
         "--select", "+gold_trial_balance",
         "--project-dir", path, "--profiles-dir", path],
        capture_output=True, text=True, timeout=580, cwd=path,
    )
    frappe.publish_realtime("demo_assertions_rebuilt", {"status": "done"})
