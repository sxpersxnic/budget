"""Reading camt.053 / camt.054 bank statements and importing them like a CSV file."""

import json
from datetime import date

import pytest
from test_importer import DE

from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.services import camt, importer
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput

IBAN = "CH9300762011623852957"

# Account statement, version .001.04 as Swiss banks deliver it (SPS): a card payment, a salary, the rent
# (with only an entry reference), a pending payment and a batch payment of three bills.
CAMT053 = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.04"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <BkToCstmrStmt>
    <GrpHdr><MsgId>MSG-2026-09</MsgId><CreDtTm>2026-10-01T06:00:00</CreDtTm></GrpHdr>
    <Stmt>
      <Id>STMT-2026-09</Id>
      <ElctrncSeqNb>9</ElctrncSeqNb>
      <CreDtTm>2026-10-01T06:00:00</CreDtTm>
      <FrToDt><FrDtTm>2026-09-01T00:00:00</FrDtTm><ToDtTm>2026-09-30T23:59:59</ToDtTm></FrToDt>
      <Acct>
        <Id><IBAN>CH93 0076 2011 6238 5295 7</IBAN></Id>
        <Ccy>CHF</Ccy>
        <Ownr><Nm>Anna Muster</Nm></Ownr>
      </Acct>
      <Bal>
        <Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
        <Amt Ccy="CHF">8200.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-08-31</Dt></Dt>
      </Bal>
      <Bal>
        <Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
        <Amt Ccy="CHF">13554.20</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-09-30</Dt></Dt>
      </Bal>
      <Ntry>
        <Amt Ccy="CHF">45.80</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-09-02</Dt></BookgDt>
        <ValDt><Dt>2026-09-02</Dt></ValDt>
        <AcctSvcrRef>ZKB-20260902-0001</AcctSvcrRef>
        <BkTxCd><Domn><Cd>PMNT</Cd><Fmly><Cd>CCRD</Cd><SubFmlyCd>POSD</SubFmlyCd></Fmly></Domn></BkTxCd>
        <NtryDtls><TxDtls>
          <Refs><EndToEndId>NOTPROVIDED</EndToEndId></Refs>
          <Amt Ccy="CHF">45.80</Amt><CdtDbtInd>DBIT</CdtDbtInd>
          <RltdPties><Cdtr><Nm>Migros Zürich HB</Nm></Cdtr></RltdPties>
          <RmtInf><Ustrd>Debitkarte 1234   Einkauf</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
        <AddtlNtryInf>Einkauf Debitkarte</AddtlNtryInf>
      </Ntry>
      <Ntry>
        <Amt Ccy="CHF">7800.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-09-25</Dt></BookgDt>
        <AcctSvcrRef>ZKB-20260925-0002</AcctSvcrRef>
        <NtryDtls><TxDtls>
          <RltdPties><Dbtr><Nm>Firma AG</Nm></Dbtr><Cdtr><Nm>Anna Muster</Nm></Cdtr></RltdPties>
          <RmtInf><Ustrd>Lohn September</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
        <AddtlNtryInf>Gutschrift Firma AG</AddtlNtryInf>
      </Ntry>
      <Ntry>
        <NtryRef>DA-2026-09</NtryRef>
        <Amt Ccy="CHF">2150.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-09-26</Dt></BookgDt>
        <AddtlNtryInf>Dauerauftrag Miete Oktober</AddtlNtryInf>
      </Ntry>
      <Ntry>
        <Amt Ccy="CHF">99.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts>PDNG</Sts>
        <BookgDt><Dt>2026-09-30</Dt></BookgDt>
        <AcctSvcrRef>ZKB-20260930-0009</AcctSvcrRef>
        <AddtlNtryInf>Vorgemerkt</AddtlNtryInf>
      </Ntry>
      <Ntry>
        <Amt Ccy="CHF">250.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-09-28</Dt></BookgDt>
        <AcctSvcrRef>ZKB-20260928-0005</AcctSvcrRef>
        <NtryDtls>
          <Btch><NbOfTxs>3</NbOfTxs></Btch>
          <TxDtls>
            <Refs><AcctSvcrRef>P1</AcctSvcrRef></Refs>
            <Amt Ccy="CHF">100.00</Amt><CdtDbtInd>DBIT</CdtDbtInd>
            <RltdPties><Cdtr><Nm>Swisscom (Schweiz) AG</Nm></Cdtr></RltdPties>
            <RmtInf><Ustrd>Rechnung 2026-09</Ustrd></RmtInf>
          </TxDtls>
          <TxDtls>
            <Amt Ccy="CHF">120.00</Amt><CdtDbtInd>DBIT</CdtDbtInd>
            <RltdPties><Cdtr><Nm>ewz</Nm></Cdtr></RltdPties>
          </TxDtls>
          <TxDtls>
            <Amt Ccy="CHF">30.00</Amt>
            <AddtlTxInf>Spende</AddtlTxInf>
          </TxDtls>
        </NtryDtls>
        <AddtlNtryInf>Sammelauftrag</AddtlNtryInf>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
""".encode()

# Debit/credit notification, version .001.08: party names under Pty, status as a code, booking date
# with a time, entries without any reference, a pending entry, a payment in EUR, and a batch whose
# details carry no amounts (stays one row).
CAMT054 = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.054.001.08">
  <BkToCstmrDbtCdtNtfctn>
    <GrpHdr><MsgId>NTF-1</MsgId><CreDtTm>2026-09-16T08:00:00+02:00</CreDtTm></GrpHdr>
    <Ntfctn>
      <Id>NTF-1</Id>
      <Acct><Id><IBAN>CH9300762011623852957</IBAN></Id></Acct>
      <Ntry>
        <Amt Ccy="CHF">120.50</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><DtTm>2026-09-15T10:12:00+02:00</DtTm></BookgDt>
        <AcctSvcrRef>QR-0001</AcctSvcrRef>
        <NtryDtls><TxDtls>
          <RltdPties>
            <Dbtr><Pty><Nm>Peter Beispiel</Nm></Pty></Dbtr>
            <Cdtr><Pty><Nm>Anna Muster</Nm></Pty></Cdtr>
          </RltdPties>
          <RmtInf><Ustrd>Anteil Ferienwohnung</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="CHF">4.20</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><Dt>2026-09-15</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <RltdPties><Cdtr><Pty><Nm>SBB CFF FFS</Nm></Pty></Cdtr></RltdPties>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="CHF">4.20</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><Dt>2026-09-15</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <RltdPties><Cdtr><Pty><Nm>SBB CFF FFS</Nm></Pty></Cdtr></RltdPties>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="CHF">60.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts><Cd>PDNG</Cd></Sts>
        <BookgDt><Dt>2026-09-16</Dt></BookgDt>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">30.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><Dt>2026-09-16</Dt></BookgDt>
        <AcctSvcrRef>EUR-1</AcctSvcrRef>
        <AddtlNtryInf>Booking.com</AddtlNtryInf>
      </Ntry>
      <Ntry>
        <Amt Ccy="CHF">80.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><Dt>2026-09-16</Dt></BookgDt>
        <AcctSvcrRef>B-2</AcctSvcrRef>
        <NtryDtls>
          <TxDtls><RltdPties><UltmtCdtr><Nm>Verein A</Nm></UltmtCdtr></RltdPties></TxDtls>
          <TxDtls><RltdPties><Cdtr><Pty><Nm>Verein B</Nm></Pty></Cdtr></RltdPties></TxDtls>
        </NtryDtls>
        <AddtlNtryInf>Sammelzahlung</AddtlNtryInf>
      </Ntry>
    </Ntfctn>
  </BkToCstmrDbtCdtNtfctn>
</Document>
"""

# An older version (.001.02) under a namespace prefix: amounts of batch details under AmtDtls, an
# account with a non-IBAN id, no account currency and no period (taken from the entries).
CAMT053_V02 = b"""<?xml version="1.0" encoding="UTF-8"?>
<ns2:Document xmlns:ns2="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <ns2:BkToCstmrStmt>
    <ns2:Stmt>
      <ns2:Acct><ns2:Id><ns2:Othr><ns2:Id>0235-00123456.01</ns2:Id></ns2:Othr></ns2:Id></ns2:Acct>
      <ns2:Bal>
        <ns2:Tp><ns2:CdOrPrtry><ns2:Cd>CLBD</ns2:Cd></ns2:CdOrPrtry></ns2:Tp>
        <ns2:Amt Ccy="CHF">15.00</ns2:Amt><ns2:CdtDbtInd>DBIT</ns2:CdtDbtInd>
        <ns2:Dt><ns2:DtTm>2026-09-05T23:59:59</ns2:DtTm></ns2:Dt>
      </ns2:Bal>
      <ns2:Ntry>
        <ns2:Amt Ccy="CHF">70.00</ns2:Amt>
        <ns2:CdtDbtInd>DBIT</ns2:CdtDbtInd>
        <ns2:BookgDt><ns2:Dt>2026-09-03</ns2:Dt></ns2:BookgDt>
        <ns2:AcctSvcrRef>OLD-1</ns2:AcctSvcrRef>
        <ns2:NtryDtls>
          <ns2:TxDtls>
            <ns2:AmtDtls><ns2:TxAmt><ns2:Amt Ccy="CHF">50.00</ns2:Amt></ns2:TxAmt></ns2:AmtDtls>
            <ns2:RmtInf><ns2:Ustrd>Teil 1</ns2:Ustrd><ns2:Ustrd>und mehr</ns2:Ustrd></ns2:RmtInf>
          </ns2:TxDtls>
          <ns2:TxDtls>
            <ns2:AmtDtls><ns2:TxAmt><ns2:Amt Ccy="CHF">20.00</ns2:Amt></ns2:TxAmt></ns2:AmtDtls>
          </ns2:TxDtls>
        </ns2:NtryDtls>
        <ns2:AddtlNtryInf>Sammelbuchung</ns2:AddtlNtryInf>
      </ns2:Ntry>
      <ns2:Ntry>
        <ns2:Amt Ccy="CHF">12.00</ns2:Amt>
        <ns2:CdtDbtInd>CRDT</ns2:CdtDbtInd>
        <ns2:ValDt><ns2:Dt>2026-09-05</ns2:Dt></ns2:ValDt>
      </ns2:Ntry>
    </ns2:Stmt>
  </ns2:BkToCstmrStmt>
</ns2:Document>
"""


def statement(body: str, ns: str = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.04") -> bytes:
    """A small camt.053 file around `body` (the entries)."""
    return (
        f'<Document xmlns="{ns}"><BkToCstmrStmt><Stmt>'
        f"<Acct><Id><IBAN>{IBAN}</IBAN></Id><Ccy>CHF</Ccy></Acct>{body}"
        "</Stmt></BkToCstmrStmt></Document>"
    ).encode()


def entry(amount: str = "10.00", ind: str = "DBIT", day: str = "2026-09-10", ref: str = "R1", extra: str = "") -> str:
    ref_xml = f"<AcctSvcrRef>{ref}</AcctSvcrRef>" if ref else ""
    return (
        f'<Ntry><Amt Ccy="CHF">{amount}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
        f"<BookgDt><Dt>{day}</Dt></BookgDt>{ref_xml}{extra}</Ntry>"
    )


# ------------------------------------------------------------------ reading
def test_reads_an_account_statement():
    st = camt.read_statement("september.xml", CAMT053)
    assert (st.message, st.iban, st.currency) == ("camt.053.001.04", IBAN, "CHF")
    assert (st.start, st.end, st.pending) == (date(2026, 9, 1), date(2026, 9, 30), 1)
    assert st.closing_balance == camt.Balance(13554.20, "CHF", date(2026, 9, 30))
    assert [(r.index, r.date, r.amount, r.text, r.import_ref) for r in st.rows] == [
        (0, date(2026, 9, 2), -45.8, "Migros Zürich HB · Debitkarte 1234 Einkauf · Einkauf Debitkarte",
         f"camt:{IBAN}:ZKB-20260902-0001"),
        (1, date(2026, 9, 25), 7800.0, "Firma AG · Lohn September · Gutschrift Firma AG",
         f"camt:{IBAN}:ZKB-20260925-0002"),
        (2, date(2026, 9, 26), -2150.0, "Dauerauftrag Miete Oktober", f"camt:{IBAN}:DA-2026-09"),
        # the batch: one row per bill, with the bill's own or a running reference
        (3, date(2026, 9, 28), -100.0, "Swisscom (Schweiz) AG · Rechnung 2026-09", f"camt:{IBAN}:ZKB-20260928-0005/P1"),
        (4, date(2026, 9, 28), -120.0, "ewz", f"camt:{IBAN}:ZKB-20260928-0005/2"),
        (5, date(2026, 9, 28), -30.0, "Spende · Sammelauftrag", f"camt:{IBAN}:ZKB-20260928-0005/3"),
    ]  # fmt: skip
    assert all(r.status == importer.NEW and r.include for r in st.rows)
    assert [r.type for r in st.rows[:2]] == [TX_EXPENDITURE, TX_INCOME]


def test_reads_a_notification_of_version_8():
    st = camt.read_statement("ntf.xml", CAMT054)
    assert (st.message, st.iban, st.currency, st.pending) == ("camt.054.001.08", IBAN, "CHF", 1)
    assert (st.start, st.end, st.closing_balance) == (date(2026, 9, 15), date(2026, 9, 16), None)
    first, sbb, sbb_again, eur, batch = st.rows
    assert (first.date, first.amount, first.text) == (date(2026, 9, 15), 120.5, "Peter Beispiel · Anteil Ferienwohnung")
    # no reference: a hash of the content, and identical entries stay two rows
    assert (sbb.amount, sbb.text) == (-4.2, "SBB CFF FFS")
    assert sbb.import_ref.startswith(f"camt:{IBAN}:h") and sbb_again.import_ref == sbb.import_ref + "#2"
    assert camt.read_statement("again.xml", CAMT054).rows[1].import_ref == sbb.import_ref  # stable
    assert (eur.status, eur.include, eur.error) == (
        importer.ERROR,
        False,
        "Amount in EUR, not in the statement's currency",
    )
    # details without amounts: one row for the whole batch
    assert (batch.amount, batch.text) == (-80.0, "Verein A · Sammelzahlung")


def test_reads_an_older_version_under_a_namespace_prefix():
    st = camt.read_statement("old.xml", CAMT053_V02)
    assert (st.message, st.iban, st.currency) == ("camt.053.001.02", "0235-00123456.01", "CHF")
    assert (st.start, st.end) == (date(2026, 9, 3), date(2026, 9, 5))  # no period: the entries' dates
    assert st.closing_balance == camt.Balance(-15.0, "CHF", date(2026, 9, 5))
    assert [(r.date, r.amount, r.text) for r in st.rows] == [
        (date(2026, 9, 3), -50.0, "Teil 1 und mehr"),
        (date(2026, 9, 3), -20.0, "Sammelbuchung"),
        (date(2026, 9, 5), 12.0, ""),  # no booking date: the value date
    ]


def test_reads_files_without_namespace_and_unknown_versions():
    plain = statement(entry(), ns="").replace(b' xmlns=""', b"")
    assert camt.read_statement("plain.xml", plain).message == "camt.053"
    other = statement(entry(), ns="http://example.com/bank/camt.053.001.10")
    assert camt.read_statement("other.xml", other).message == "camt.053.001.10"
    # the namespace names another message: trust the content
    wrong = statement(entry(), ns="urn:iso:std:iso:20022:tech:xsd:camt.052.001.04")
    assert camt.read_statement("wrong.xml", wrong).message == "camt.053"
    bare = b"<BkToCstmrStmt><Stmt>" + entry().encode() + b"</Stmt></BkToCstmrStmt>"
    st = camt.read_statement("bare.xml", bare)  # no Document wrapper, no account: currency from the entry
    assert (st.iban, st.currency, st.rows[0].import_ref) == ("", "CHF", "camt::R1")
    no_currency = bare.replace(b' Ccy="CHF"', b"")
    st = camt.read_statement("bare.xml", no_currency)  # no currency anywhere: any account will do
    assert (st.currency, st.rows[0].amount, st.rows[0].status) == ("", -10.0, importer.NEW)


def test_a_batch_that_does_not_add_up_stays_one_row():
    details = (
        '<NtryDtls><TxDtls><Amt Ccy="CHF">6.00</Amt></TxDtls><TxDtls><Amt Ccy="CHF">3.00</Amt></TxDtls></NtryDtls>'
    )
    st = camt.read_statement("x.xml", statement(entry(extra=details)))
    assert [r.amount for r in st.rows] == [-10.0]
    other_currency = details.replace('"CHF">3.00', '"EUR">4.00')
    assert len(camt.read_statement("x.xml", statement(entry(extra=other_currency))).rows) == 1
    # without a reference the parts are told apart by their content
    split = details.replace("3.00", "4.00")
    rows = camt.read_statement("x.xml", statement(entry(ref="", extra=split))).rows
    assert [r.amount for r in rows] == [-6.0, -4.0] and rows[0].import_ref != rows[1].import_ref


def test_entries_without_date_or_amount_are_unreadable():
    body = (
        '<Ntry><Amt Ccy="CHF">5.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><AcctSvcrRef>A</AcctSvcrRef></Ntry>'
        + entry(amount="abc", ref="B")
        + entry(ind="", ref="C")
        + entry(amount="0.00", ref="D")
        + "<Ntry><CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>2026-09-10</Dt></BookgDt><NtryRef>E</NtryRef></Ntry>"
    )
    rows = camt.read_statement("x.xml", statement(body)).rows
    assert [r.error for r in rows] == ["No valid date"] + ["No valid amount"] * 4
    assert all(r.status == importer.ERROR and not r.include for r in rows)


def test_merges_statements_of_one_account_and_keeps_the_last_closing_balance():
    def stmt(day: str, closing: str, ref: str) -> str:
        return (
            f"<Stmt><Acct><Id><IBAN>{IBAN}</IBAN></Id></Acct>"
            f'<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="CHF">{closing}</Amt>'
            f"<CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>{day}</Dt></Dt></Bal>"
            f'<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="CHF">x</Amt></Bal>'
            f"{entry(day=day, ref=ref)}</Stmt>"
        )

    data = f"<Document><BkToCstmrStmt>{stmt('2026-09-30', '20.00', 'B')}{stmt('2026-08-31', '10.00', 'A')}"
    st = camt.read_statement("x.xml", (data + "</BkToCstmrStmt></Document>").encode())
    assert [r.import_ref for r in st.rows] == [f"camt:{IBAN}:B", f"camt:{IBAN}:A"]
    assert (st.start, st.end) == (date(2026, 8, 31), date(2026, 9, 30))
    assert st.closing_balance == camt.Balance(20.0, "CHF", date(2026, 9, 30))


# ------------------------------------------------------------------ bad files
@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<Document><BkToCstmrStmt>", "The file is not valid XML (line 1)"),
        (b"<?xml version='1.0'?>\n<a>\n<b></a>", "The file is not valid XML (line 3)"),
        (
            b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
            b"<Document>&lol2;</Document>",
            "The XML file declares a document type, which bank statements never do",
        ),
        (
            b'<!DOCTYPE d SYSTEM "file:///etc/passwd"><Document/>',
            "The XML file declares a document type, which bank statements never do",
        ),
        (
            b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"><CstmrCdtTrfInitn/></Document>',
            "The XML file is not a camt.053 or camt.054 bank statement",
        ),
        (statement(""), "The statement has no booked entries"),
        (statement(entry().replace("<BookgDt>", "<Sts>PDNG</Sts><BookgDt>")), "The statement has no booked entries"),
    ],
)
def test_refuses_files_that_are_not_statements(data, message):
    with pytest.raises(importer.ImportFileError) as exc:
        camt.read_statement("bad.xml", data)
    assert str(exc.value) == message


def test_refuses_statements_of_several_accounts_and_large_files():
    two = (
        f"<Document><BkToCstmrStmt><Stmt><Acct><Id><IBAN>{IBAN}</IBAN></Id></Acct>{entry()}</Stmt>"
        f"<Stmt><Acct><Id><IBAN>CH5604835012345678009</IBAN></Id></Acct>{entry()}</Stmt>"
        "</BkToCstmrStmt></Document>"
    ).encode()
    with pytest.raises(importer.ImportFileError, match="several accounts"):
        camt.read_statement("two.xml", two)
    with pytest.raises(importer.ImportFileError, match="20 MB"):
        camt.read_statement("big.xml", b"<" + b" " * importer.MAX_BYTES + b">")


def test_helpers():
    assert camt.looks_like_xml(b"\xef\xbb\xbf\n  <?xml version='1.0'?><Document/>")
    assert not camt.looks_like_xml(b"Datum;Betrag\n")
    assert camt.format_iban(IBAN) == "CH93 0076 2011 6238 5295 7"


# ------------------------------------------------------------------ importing
def categorise(rows: list[importer.ImportRow], salary: str) -> None:
    for r in rows:
        r.category = salary if r.type == TX_INCOME else "01.1"


def test_import_marks_duplicates_and_remembers_the_account(ctx, setup):
    chf = setup["chf"].id
    st = camt.read_statement("september.xml", CAMT053)
    assert camt.remembered_account(ctx.store.data_dir, IBAN) == ""
    rows = importer.analyse(ctx, st.fresh_rows(), chf)
    assert [r.import_ref for r in rows] == [r.import_ref for r in st.rows]  # analyse keeps the bank's reference
    categorise(rows, setup["salary"].id)
    saved = camt.commit(ctx, st, rows, chf)
    assert len(saved) == 6 and st.rows[0].category == ""  # the statement's own rows are untouched
    assert (saved[0].type, saved[0].amount, saved[0].currency, saved[0].from_account) == (
        TX_EXPENDITURE,
        45.8,
        "CHF",
        chf,
    )
    assert (saved[1].type, saved[1].to_account, saved[1].comment) == (TX_INCOME, chf, st.rows[1].text)
    assert camt.remembered_account(ctx.store.data_dir, IBAN) == chf
    assert json.loads((ctx.store.data_dir / camt.ACCOUNTS_FILE).read_text()) == {IBAN: chf}

    # the same file again: everything is already imported, categories are suggested
    again = importer.analyse(ctx, st.fresh_rows(), chf)
    assert {r.status for r in again} == {importer.DUPLICATE} and not any(r.include for r in again)
    assert again[0].category == "01.1" and again[0].suggested

    # an overlapping statement: only the new entry is new
    rent = entry(day="2026-09-26", amount="2150.00", ref="", extra="<NtryRef>DA-2026-09</NtryRef>")
    overlap = statement(rent + entry(ref="NEW"))
    rows = importer.analyse(ctx, camt.read_statement("overlap.xml", overlap).fresh_rows(), chf)
    assert [r.status for r in rows] == [importer.DUPLICATE, importer.NEW]


def test_a_statement_in_another_currency_is_refused(ctx, setup):
    st = camt.read_statement("september.xml", CAMT053)
    assert camt.currency_problem(ctx, st, setup["chf"].id) == ""
    assert camt.currency_problem(ctx, st, "missing") == ""
    problem = camt.currency_problem(ctx, st, setup["eur"].id)
    assert problem == "The statement is in CHF, but “Euro account” is in EUR. Choose an account in CHF."
    rows = st.fresh_rows()
    categorise(rows, setup["salary"].id)
    with pytest.raises(ValidationError, match="The statement is in CHF"):
        camt.commit(ctx, st, rows, setup["eur"].id)
    assert ctx.store.transactions.all() == []
    assert camt.remembered_account(ctx.store.data_dir, IBAN) == ""


def test_an_account_without_iban_is_not_remembered(ctx, setup):
    st = camt.read_statement("bare.xml", b"<BkToCstmrStmt><Stmt>" + entry().encode() + b"</Stmt></BkToCstmrStmt>")
    rows = importer.analyse(ctx, st.fresh_rows(), setup["chf"].id)
    categorise(rows, setup["salary"].id)
    camt.commit(ctx, st, rows, setup["chf"].id)
    assert not (ctx.store.data_dir / camt.ACCOUNTS_FILE).exists()
    assert camt.remembered_account(ctx.store.data_dir, "") == ""
    (ctx.store.data_dir / camt.ACCOUNTS_FILE).write_text("[]")  # not a mapping: ignored
    assert camt.remembered_account(ctx.store.data_dir, IBAN) == ""


def test_entries_from_a_csv_import_or_typed_by_hand_are_possible_duplicates(ctx, setup):
    chf = setup["chf"].id
    tx_svc.save(
        ctx,
        TransactionInput(date=date(2026, 9, 2), amount=45.8, currency="CHF", category="01.1", from_account=chf),
    )
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type=TX_INCOME,
            amount=7800,
            currency="CHF",
            category=setup["salary"].id,
            to_account=chf,
            import_ref="csv:0123456789abcdef0123",
        ),
    )
    rows = importer.analyse(ctx, camt.read_statement("september.xml", CAMT053).fresh_rows(), chf)
    assert [r.status for r in rows[:3]] == [importer.POSSIBLE_DUPLICATE, importer.POSSIBLE_DUPLICATE, importer.NEW]

    # and the other way round: CSV lines matching an entry imported from a statement
    st = camt.read_statement("x.xml", statement(entry(day="2026-09-15", amount="12.30")))
    rows = importer.analyse(ctx, st.fresh_rows(), chf)
    categorise(rows, setup["salary"].id)
    camt.commit(ctx, st, rows, chf)
    parsed = importer.read_file("de.csv", DE)
    csv_rows = importer.analyse(ctx, importer.parse_rows(parsed, importer.guess_profile(parsed)), chf)
    assert [r.status for r in csv_rows[:2]] == [importer.POSSIBLE_DUPLICATE, importer.NEW]
