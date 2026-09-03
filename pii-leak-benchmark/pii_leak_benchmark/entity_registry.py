"""Axis A of the corpus: the entity registry, with every entity's disposition declared.

WHERE THIS COMES FROM, AND WHY NOT FROM THE PRODUCT. The entity set is transcribed from
`.llm/research/entity-list.md`, which was derived from external authority only -- GDPR
Art. 4/9 and Recital 30, India's DPDP Act 2023 s. 2(t), NIST SP 800-122 §2.2, 45 CFR
164.514(b)(2) Safe Harbor, PCI, with ISO 3166 country spread. It was NOT derived from
`TIER1_PATTERNS`, from any recognizer registry, or from what any gateway can currently
detect.

That constraint is the whole point. An entity list drawn from a product's own recognizer
registry produces a benchmark shaped to that product's strengths, and a referee will say
so. Several entities here are present *because* no gateway is expected to detect them.
Those are the rows that make the benchmark worth citing, and a benchmark whose author's
tool scores 100% is a marketing page.

ALL 72 ENTITIES APPEAR HERE. An entity the harness cannot generate a value for, or
cannot match a format for, is recorded as a DECLARED EXCLUSION with its reason -- never
dropped. A registry that silently shrank to the generatable rows would quietly redefine
the problem as "the entities that happen to have regular expressions", which is exactly
what the external derivation was meant to prevent. The manifest publishes all 72 and the
report's `entity_scope` block must account for all 72.

DISPOSITIONS

    generatable         A value can be drawn that is well-formed, checksum-valid where
                        a checksum exists, and cannot belong to a real person.
    not-generatable     A format exists and is matchable, but no safe value can be
                        constructed: no reserved test range, and any well-formed value
                        may be someone's. Scored `not-applicable`, never `pass`.
    no-matchable-format No format a detector could match in isolation -- no rule at all,
                        or per-jurisdiction with no unifying shape. Scored
                        `not-applicable`.
    out-of-text-scope   Binary: images, audio, biometric templates. A text-stream
                        gateway cannot reach these and must be recorded as
                        `not-applicable` rather than silently passing.
    alias               Not an entity in its own right; a pointer to per-country rows.

Only `generatable` entities produce corpus cases. The other four produce manifest rows
and a declared exclusion, so a reader can see the size of what was NOT measured.

TWO DELTAS AGAINST `entity-list.md` §7, RECORDED RATHER THAN RECONCILED SILENTLY. §7
summarises "26 rows unsafe to generate" and "36 rows carry an UNVERIFIED cell". Reading
the §3 tables cell by cell gives different numbers -- see `COUNT_DELTAS` at the bottom of
this module for the exact figures and the criterion used here. The difference is a
counting-criterion difference, not new information: §7 appears to count only the rows
its §5 register names explicitly, while this module also counts rows whose synthetic
value is absent for a jurisdictional or binary reason. The registry's own counts are
computed from the data below and asserted in the tests; §7's are quoted, not trusted.
"""

from __future__ import annotations

from typing import Any, Optional

# Disposition constants, so a typo is an AttributeError rather than a silent third
# category that nothing generates and nothing excludes.
GENERATABLE = "generatable"
NOT_GENERATABLE = "not-generatable"
NO_MATCHABLE_FORMAT = "no-matchable-format"
OUT_OF_TEXT_SCOPE = "out-of-text-scope"
ALIAS = "alias"

DISPOSITIONS = (
    GENERATABLE,
    NOT_GENERATABLE,
    NO_MATCHABLE_FORMAT,
    OUT_OF_TEXT_SCOPE,
    ALIAS,
)

# Entity ids are <= 10 ASCII characters and that is a streaming constraint, not a style
# rule. The vault's look-behind retention is L = N - 1 where N is the maximum
# placeholder length, and the placeholder derives from the entity id, so every character
# on the LONGEST id widens the window the SSE rehydration buffer holds on the hot path
# for every request -- whether or not that entity is ever seen. v1's report label
# `CREDIT_CARD` is 11 characters and does not fit; the registry id is `CARDPAN`.
MAX_ENTITY_ID_LENGTH = 10


def _entity(
    entity_id: str,
    region: str,
    sources: tuple[str, ...],
    disposition: str,
    reason: str,
    *,
    format_rule: str = "",
    checksum: str = "none",
    reserved_range: Optional[str] = None,
    synthetic: Optional[str] = None,
    negative_control: Optional[str] = None,
    publishable: bool = True,
    unverified: bool = False,
    collides_with: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "region": region,
        "sources": sources,
        "disposition": disposition,
        "reason": reason,
        "format_rule": format_rule,
        "checksum": checksum,
        "reserved_range": reserved_range,
        "synthetic": synthetic,
        "negative_control": negative_control,
        "publishable": publishable,
        "unverified": unverified,
        "collides_with": collides_with,
    }


ENTITIES: tuple[dict[str, Any], ...] = (
    # ---------------------------------------------------------------- global (31)
    _entity(
        "NAME", "global", ("GDPR-4", "NIST-2.2", "HIPAA-A"), NO_MATCHABLE_FORMAT,
        "No format rule exists -- any Unicode string is a name. Detectable only by a "
        "model, never by a pattern, and the dominant false-positive source.",
        format_rule="any Unicode string",
        collides_with=("every free-text token",),
    ),
    _entity(
        "EMAIL", "global", ("HIPAA-F", "NIST-2.2"), GENERATABLE,
        "RFC 2606 s3 reserves example.com for documentation; it accepts no mail, so the "
        "address cannot reach a person, and .com is a real public suffix so validating "
        "detectors still accept it.",
        format_rule="RFC 5321 4.1.2 / RFC 5322 3.4.1 addr-spec",
        reserved_range="example.com/.net/.org, .test, .example, .invalid (RFC 2606)",
        synthetic="user@example.com",
        collides_with=("URL",),
    ),
    _entity(
        "IPV4", "global", ("GDPR-R30", "HIPAA-O", "NIST-2.2"), GENERATABLE,
        "IETF documentation ranges are a genuine standards-body reservation -- one of "
        "only five in the whole registry.",
        format_rule="dotted quad, RFC 791",
        reserved_range="192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 (RFC 5737)",
        synthetic="192.0.2.10",
        collides_with=("SSN", "ABART", "AADHAAR"),
    ),
    _entity(
        "IPV6", "global", ("GDPR-R30", "HIPAA-O", "NIST-2.2"), GENERATABLE,
        "RFC 3849 documentation prefix.",
        format_rule="RFC 4291 text representation",
        reserved_range="2001:DB8::/32 (RFC 3849)",
        synthetic="2001:db8::8a2e:370:7334",
        collides_with=("MAC", "CRYPTOW"),
    ),
    _entity(
        "MAC", "global", ("NIST-2.2",), GENERATABLE,
        "RFC 7042 2.1.2 reserves documentation ranges for both unicast and multicast.",
        format_rule="EUI-48, IEEE 802",
        reserved_range="00-00-5E-00-53-00..FF unicast (RFC 7042 2.1.2)",
        synthetic="00-00-5E-00-53-01",
        collides_with=("IPV6", "DEVSERIAL"),
    ),
    _entity(
        "URL", "global", ("HIPAA-N",), GENERATABLE,
        "Same RFC 2606 reservation as EMAIL.",
        format_rule="RFC 3986",
        reserved_range="example.com etc. (RFC 2606)",
        synthetic="https://example.com/u/1",
        collides_with=("EMAIL", "COOKIE"),
    ),
    _entity(
        "COOKIE", "global", ("GDPR-R30",), NO_MATCHABLE_FORMAT,
        "Opaque and issuer-defined. Indistinguishable from a session token, API key or "
        "nonce; only entropy scoring can see it, and it collides with every secret.",
        collides_with=("every opaque token",),
    ),
    _entity(
        "RFIDTAG", "global", ("GDPR-R30",), NO_MATCHABLE_FORMAT,
        "EPC / ISO-IEC 15693 UID format could not be retrieved, so no matchable rule is "
        "established. Expect no gateway to handle it, and publish that.",
        unverified=True,
        collides_with=("MAC", "DEVSERIAL"),
    ),
    _entity(
        "IMEI", "global", ("HIPAA-M", "NIST-2.2"), GENERATABLE,
        "Repeated-digit value computed to satisfy Luhn here, not cited.",
        format_rule="15 digits: 8-digit TAC + 6-digit SNR + check digit",
        checksum="Luhn over the 14 most significant digits (verified)",
        synthetic="999999999999994",
        collides_with=("IMSI", "CARDPAN"),
    ),
    _entity(
        "IMSI", "global", ("GDPR-4", "HIPAA-R"), NOT_GENERATABLE,
        "No checksum to make a value provably synthetic, and the MCC 001 / MNC 01 test "
        "network is unverified -- ITU-T E.212 was not retrieved. A well-formed IMSI may "
        "be live.",
        format_rule="3-digit MCC + 2-3-digit MNC + MSIN, <= 15 digits",
        unverified=True,
        collides_with=("IMEI", "CARDPAN"),
    ),
    _entity(
        "CARDPAN", "global", ("PCI", "NIST-2.2"), GENERATABLE,
        "Drawn from a published brand test list, never generated: a randomly generated "
        "Luhn-valid PAN in an issued BIN may be a live card. Note these are BRAND-"
        "published, not a standards-body reserved range.",
        format_rule="ISO/IEC 7812-1, 12-19 digits",
        checksum="Luhn (verified)",
        reserved_range="brand-published test PANs (not a standards reservation)",
        synthetic="4111111111111111",
        collides_with=("AADHAAR", "IMSI", "IMEI"),
    ),
    _entity(
        "CVV", "global", ("PCI",), NOT_GENERATABLE,
        "Three digits carry no identity, so the risk is not the value -- it is the "
        "claim. A bare 3-digit run collides with every 3-digit number in any text, so "
        "it is detectable only in context and a gateway claiming standalone CVV "
        "detection is over-claiming. Present so the benchmark records that, not so it "
        "can be scored.",
        format_rule="3 digits (4 for Amex)",
        collides_with=("every 3-digit run",),
    ),
    _entity(
        "CARDEXP", "global", ("PCI",), GENERATABLE,
        "An expiry pair identifies nobody on its own. 12/99 rather than the obvious "
        "12/34: normalized to `1234` it is a substring of the IBAN, the SSN and the "
        "NINO synthetic values, and a case carrying any of those alongside it would "
        "report CARDEXP leaked whenever the longer value leaked. Caught by the "
        "containment check, not by inspection.",
        format_rule="MM/YY or MM/YYYY",
        synthetic="12/99",
        collides_with=("DOB", "version strings"),
    ),
    _entity(
        "IBAN", "global", ("GDPR-4", "NIST-2.2"), GENERATABLE,
        "The ISO/ECBS published example, with its MOD 97-10 arithmetic re-derived here.",
        format_rule="ISO 13616, 2-char country + 2 check digits + BBAN, <= 34 chars",
        checksum="ISO 7064 MOD 97-10 (verified)",
        synthetic="GB82WEST12345698765432",
        collides_with=("BIC",),
    ),
    _entity(
        "BIC", "global", ("GDPR-4",), NOT_GENERATABLE,
        "No checksum and no reserved range: any well-formed BIC may be a live "
        "institution code.",
        format_rule="ISO 9362",
        collides_with=("IBAN",),
    ),
    _entity(
        "DOB", "global", ("HIPAA-C", "NIST-2.2"), GENERATABLE,
        "An epoch date is unambiguously synthetic.",
        format_rule="ISO 8601 and every locale variant",
        synthetic="1970-01-01",
        collides_with=("CARDEXP", "every other date"),
    ),
    _entity(
        "PASSPORT", "global", ("NIST-2.2",), NOT_GENERATABLE,
        "Issuer-defined, no reserved range, and ICAO 9303 was not retrieved. A "
        "well-formed number may be live.",
        format_rule="issuer-defined, <= 9 alphanumeric in the MRZ",
        unverified=True,
        collides_with=("USDL", "DEVSERIAL"),
    ),
    _entity(
        "MRZ", "global", ("NIST-2.2", "HIPAA-R"), NOT_GENERATABLE,
        "Same unretrieved source as PASSPORT. Worth keeping in the registry: the '<' "
        "filler survives base64 and fragmentation badly, which makes it an interesting "
        "Axis B/C case the moment it becomes generatable.",
        format_rule="2 or 3 fixed-width lines, [A-Z0-9<]",
        checksum="7-3-1 weighted mod 10 per field plus composite (unverified)",
        unverified=True,
    ),
    _entity(
        "VIN", "global", ("HIPAA-L", "NIST-2.2"), GENERATABLE,
        "NHTSA's own worked example, arithmetic reproduced from 49 CFR 565 Table VI.",
        format_rule="17 chars from [A-HJ-NPR-Z0-9], check digit at position 9",
        checksum="weighted mod 11, X for remainder 10 (verified against the regulation)",
        synthetic="1G4AH59H45G118341",
        collides_with=("DEVSERIAL",),
    ),
    _entity(
        "PLATE", "global", ("HIPAA-L",), NO_MATCHABLE_FORMAT,
        "Per-jurisdiction with no unifying rule: a US, UK and Indian plate share no "
        "shape. Expect no gateway to handle the general case.",
        collides_with=("everything",),
    ),
    _entity(
        "DEVSERIAL", "global", ("HIPAA-M", "NIST-2.2"), NO_MATCHABLE_FORMAT,
        "Vendor-defined; no format rule exists.",
        collides_with=("VIN", "MAC", "PASSPORT"),
    ),
    _entity(
        "CRYPTOW", "global", ("GDPR-4",), NOT_GENERATABLE,
        "A valid address may hold funds. The checksum rules come from BIPs and EIPs, "
        "which are neither government nor standards-body sources.",
        format_rule="Base58Check / bech32, or 0x + 40 hex",
        checksum="Base58Check, bech32 BCH, EIP-55 (unverified)",
        unverified=True,
        collides_with=("MAC", "IPV6"),
    ),
    _entity(
        "GEOCOORD", "global", ("GDPR-4", "HIPAA-B"), GENERATABLE,
        "Null Island identifies nobody.",
        format_rule="decimal degrees or DMS",
        synthetic="0.0000, 0.0000",
        collides_with=("version numbers", "float pairs"),
    ),
    _entity(
        "STREET", "global", ("HIPAA-B", "NIST-2.2"), NO_MATCHABLE_FORMAT,
        "No format rule; locale-dependent. A synthetic value exists but there is no "
        "shape to match it by, so it cannot be scored as a detection.",
        synthetic="1 Example Street",
        collides_with=("free text",),
    ),
    _entity(
        "POSTCODE", "global", ("HIPAA-B",), ALIAS,
        "Not an entity in its own right -- a pointer to ZIP5, UKPOST and CAPOST. Kept "
        "so the HIPAA Safe Harbor identifier it names is visibly accounted for.",
    ),
    _entity(
        "BIOTMPL", "global", ("GDPR-9", "HIPAA-P", "NIST-2.2"), OUT_OF_TEXT_SCOPE,
        "ISO/IEC 19794 templates are binary and reach an LLM only as base64 or hex. "
        "Bounded base64 inspection stops at 8,192 chars, so a template above that is "
        "inspected only at its boundary guards. Expect this to be missed, and publish "
        "that rather than omitting the row.",
        collides_with=("base64 blobs",),
    ),
    _entity(
        "FACEIMG", "global", ("HIPAA-Q", "GDPR-9"), OUT_OF_TEXT_SCOPE,
        "Image bytes. The gateway's own invariant is 'no image OCR', so this is a "
        "DECLARED out-of-scope row: it must report not-applicable, never a silent pass.",
    ),
    _entity(
        "VOICEPR", "global", ("HIPAA-P", "NIST-2.2"), OUT_OF_TEXT_SCOPE,
        "Audio or feature vector; out of scope for a text gateway, and recorded as such.",
    ),
    _entity(
        "GENETIC", "global", ("GDPR-9", "GDPR-4"), GENERATABLE,
        "A zero-filled rs accession is shape-only and refers to no variant. Almost "
        "certainly undetected -- FASTA is [ACGT]+ and indistinguishable from a hex-ish "
        "token -- which is a result worth publishing.",
        format_rule="HGVS nomenclature, dbSNP rs accessions, raw FASTA",
        synthetic="rs0000000",
        collides_with=("hex-ish tokens",),
    ),
    _entity(
        "HEALTHDX", "global", ("GDPR-9",), GENERATABLE,
        "Z00.00 is the 'no diagnosis' code and describes nobody.",
        format_rule="ICD-10-CM: letter + 2 digits + optional . + up to 4 alphanumerics",
        synthetic="Z00.00",
        unverified=True,
        collides_with=("product SKUs", "grid references"),
    ),
    _entity(
        "ART9CAT", "global", ("GDPR-9",), NO_MATCHABLE_FORMAT,
        "Racial or ethnic origin, political opinions, religious or philosophical "
        "beliefs, trade union membership, sex life, sexual orientation. No format rule "
        "at all, yet Art. 9 makes these the MOST protected class. The honest benchmark "
        "outcome is not-applicable, and saying so is more useful than a pass.",
    ),
    # -------------------------------------------------------------------- us (13)
    _entity(
        "SSN", "us", ("HIPAA-G", "NIST-2.2"), GENERATABLE,
        "The SSA has never issued a 900-series area, so a value there cannot belong to "
        "anyone. The group is held outside every ITIN range so a validating detector "
        "does not correctly relabel it -- see the ITIN row.",
        format_rule="9 digits AAA-GG-SSSS",
        reserved_range="900-series never assigned (POMS RM 10201.035)",
        synthetic="900-12-3456",
        collides_with=("ITIN", "ABART", "EIN", "CASIN", "AUTFN", "IPV4"),
    ),
    _entity(
        "ITIN", "us", ("NIST-2.2",), NOT_GENERATABLE,
        "Every well-formed ITIN is issuable and the IRS publishes no reserved block. "
        "The IRS's own literal placeholder 9XX-7X-XXXX is not a number and will not "
        "exercise a validating detector -- which is the honest position.",
        format_rule="9 digits, 9XX-7X-XXXX, 4th-5th in 50-65, 70-88, 90-92, 94-99",
        collides_with=("SSN",),
    ),
    _entity(
        "EIN", "us", ("NIST-2.2",), NOT_GENERATABLE,
        "Sole-proprietor EINs are personal data and there is no reserved block.",
        format_rule="9 digits NN-NNNNNNN",
        collides_with=("SSN", "ITIN", "ABART"),
    ),
    _entity(
        "NPI", "us", ("HIPAA-K",), NOT_GENERATABLE,
        "No reserved block. Keep the checksum note: NPI is Luhn with the constant 24 "
        "added to stand in for the 80840 prefix, so a detector applying PLAIN Luhn to a "
        "bare NPI rejects every real one.",
        format_rule="10 digits, first digit 1 or 2, 10th is the check digit",
        checksum="Luhn + 24 (verified)",
        collides_with=("NHSNUM", "USPHONE"),
    ),
    _entity(
        "DEANUM", "us", ("HIPAA-K",), NOT_GENERATABLE,
        "The DEA Diversion Control source returned 404, so both the format and the "
        "check-digit rule are unverified. Generating against an unverified algorithm "
        "would produce a value nobody can show is synthetic.",
        format_rule="2 letters + 7 digits (unverified)",
        unverified=True,
        collides_with=("USDL",),
    ),
    _entity(
        "MRN", "us", ("HIPAA-H", "NIST-2.2"), NO_MATCHABLE_FORMAT,
        "No national format exists -- an MRN is whatever a hospital says it is. The "
        "single hardest row, and in the registry for exactly that reason.",
        collides_with=("everything",),
    ),
    _entity(
        "MBI", "us", ("HIPAA-I",), NOT_GENERATABLE,
        "CMS is documented as publishing a test-MBI block, but cms.gov returned 403 and "
        "the block could not be confirmed. Pending that, no value is invented.",
        format_rule="11 position-constrained alphanumerics, excludes S,L,O,I,B,Z",
        unverified=True,
        collides_with=("VIN", "HPBN"),
    ),
    _entity(
        "HPBN", "us", ("HIPAA-I",), NO_MATCHABLE_FORMAT,
        "Payer-defined with no unifying format.",
        collides_with=("MRN",),
    ),
    _entity(
        "USDL", "us", ("NIST-2.2", "HIPAA-K"), NO_MATCHABLE_FORMAT,
        "Fifty-one different state formats and AAMVA was not retrieved. A gateway "
        "offering 'US driver's licence' as one entity is over-claiming.",
        unverified=True,
        collides_with=("PASSPORT", "DEANUM"),
    ),
    _entity(
        "ABART", "us", ("NIST-2.2",), GENERATABLE,
        "000000000 satisfies the 3-7-1 weighting, verified here. The reason it is SAFE "
        "-- that no Federal Reserve district is numbered 00 -- is unverified, and that "
        "caveat travels with the value.",
        format_rule="9 digits; first two encode the Federal Reserve routing symbol",
        checksum="3-7-1 mod 10 (verified)",
        synthetic="000000000",
        unverified=True,
        collides_with=("SSN", "EIN", "ITIN", "CASIN", "AUTFN"),
    ),
    _entity(
        "USACCT", "us", ("HIPAA-J", "NIST-2.2"), NO_MATCHABLE_FORMAT,
        "No format rule exists: 4-17 digits, bank-defined. Pairs with ABART in context; "
        "alone it is every digit run.",
        collides_with=("every digit run",),
    ),
    _entity(
        "USPHONE", "us", ("HIPAA-D", "HIPAA-E", "NIST-2.2"), GENERATABLE,
        "The 555-01XX fictitious block. Its source (ATIS/INC via NANPA) was unreachable, "
        "so the reservation itself is unverified even though the value is well-formed.",
        format_rule="NANP NXX-NXX-XXXX, N in 2-9",
        reserved_range="555-0100..555-0199 fictitious (unverified)",
        synthetic="(555) 555-0123",
        unverified=True,
        collides_with=("NHSNUM", "NPI", "IPV4"),
    ),
    _entity(
        "ZIP5", "us", ("HIPAA-B",), GENERATABLE,
        "00000 is never assigned.",
        format_rule="5 digits, or ZIP+4",
        synthetic="00000",
        collides_with=("CVV", "years"),
    ),
    # -------------------------------------------------------------------- uk (6)
    _entity(
        "NINO", "uk", ("GDPR-4", "NIST-2.2"), GENERATABLE,
        "HMRC's own published example QQ123456A. Note the subtlety and score it "
        "accordingly: Q is a barred first letter, so HMRC's example is deliberately NOT "
        "a well-formed NINO. A detector enforcing the prefix rules will reject it, and "
        "that is CORRECT behaviour that must not be scored as a miss.",
        format_rule="2 letters + 6 digits + suffix A-D, with barred prefixes",
        reserved_range="HMRC published example (not a reserved range)",
        synthetic="QQ123456A",
        collides_with=("UKUTR", "DEANUM"),
    ),
    _entity(
        "NHSNUM", "uk", ("GDPR-4",), GENERATABLE,
        "Checksum computed here. Note 999000000 yields a mod-11 result of 10, which is "
        "not issuable -- the synthetic value avoids it.",
        format_rule="10 digits, 10th is a check digit",
        checksum="Modulus 11, weights 10..2 (verified)",
        reserved_range="999 000 0000 - 999 999 9999 synthetic patients (unverified)",
        synthetic="9990000018",
        unverified=True,
        collides_with=("USPHONE", "NPI", "CAHCN"),
    ),
    _entity(
        "UKUTR", "uk", ("NIST-2.2",), NOT_GENERATABLE,
        "HMRC's format page was not retrieved and the weighted mod 11 rule is "
        "unverified.",
        format_rule="10 digits (unverified)",
        unverified=True,
        collides_with=("NHSNUM", "NPI", "USPHONE"),
    ),
    _entity(
        "UKPHONE", "uk", ("HIPAA-D", "NIST-2.2"), GENERATABLE,
        "Ofcom drama ranges. ofcom.org.uk returned 403 on four URLs, so the reservation "
        "is unverified.",
        format_rule="E.164 +44, or national 0 + 9-10 digits",
        reserved_range="07700 900000-900999, 020 7946 0000-0999 (unverified)",
        synthetic="+44 7700 900123",
        unverified=True,
    ),
    _entity(
        "UKPOST", "uk", ("HIPAA-B", "GDPR-4"), GENERATABLE,
        "A ZZ99 pattern rather than SW1A 1AA, which is a real government postcode.",
        format_rule="outward + inward, AA9A 9AA and five other patterns",
        synthetic="ZZ99 9ZZ",
        unverified=True,
        collides_with=("alphanumeric tokens",),
    ),
    _entity(
        "UKSORT", "uk", ("NIST-2.2",), GENERATABLE,
        "No checksum exists, so 00-00-00 is as synthetic as anything can be. Worth the "
        "row for the collision alone: a sort code and a DD-MM-YY date are the same "
        "shape.",
        format_rule="6 digits NN-NN-NN",
        synthetic="00-00-00",
        unverified=True,
        collides_with=("DOB",),
    ),
    # -------------------------------------------------------------------- eu (7)
    _entity(
        "VATEU", "eu", ("GDPR-4",), NOT_GENERATABLE,
        "A sole trader's VAT number is personal data, checksums differ per member state "
        "and are unverified, and VIES validates against live registers rather than "
        "publishing a test range.",
        format_rule="2-char ISO 3166 country prefix + 2-12 national characters",
        unverified=True,
        collides_with=("IBAN", "BIC"),
    ),
    _entity(
        "DESTID", "de", ("GDPR-4",), NOT_GENERATABLE,
        "Format and ISO 7064 MOD 11,10 checksum both unverified.",
        format_rule="11 digits, first digit non-zero (unverified)",
        unverified=True,
        collides_with=("AADHAAR", "AUABN", "PLPESEL"),
    ),
    _entity(
        "FRNIR", "fr", ("GDPR-4", "GDPR-9"), NOT_GENERATABLE,
        "Unverified, and it encodes sex and historically place of birth -- so a "
        "well-formed value is Art. 9 data, not merely an identifier.",
        format_rule="13 digits + 2-digit key (unverified)",
        unverified=True,
        collides_with=("IMEI", "IMSI", "CARDPAN"),
    ),
    _entity(
        "ESDNI", "es", ("GDPR-4",), NOT_GENERATABLE,
        "Mod 23 letter table unverified.",
        format_rule="8 digits + 1 letter (unverified)",
        unverified=True,
        collides_with=("NINO", "SGPHONE"),
    ),
    _entity(
        "ITCF", "it", ("GDPR-4", "GDPR-9"), NOT_GENERATABLE,
        "Unverified, and derived from name, DOB, sex and birthplace -- it encodes Art. 9 "
        "data by construction.",
        format_rule="16 alphanumerics (unverified)",
        unverified=True,
        collides_with=("MBI", "CARDPAN", "INVID"),
    ),
    _entity(
        "PLPESEL", "pl", ("GDPR-4", "GDPR-9"), NOT_GENERATABLE,
        "Unverified. Its first six digits ARE a date, which makes it collide with DOB "
        "by construction rather than by accident.",
        format_rule="11 digits, first six YYMMDD (unverified)",
        unverified=True,
        collides_with=("DESTID", "DOB"),
    ),
    _entity(
        "NLBSN", "nl", ("GDPR-4",), NOT_GENERATABLE,
        "The elfproef weighting is unverified.",
        format_rule="9 digits (unverified)",
        unverified=True,
        collides_with=("SSN", "EIN", "ABART", "CASIN", "AUTFN"),
    ),
    # -------------------------------------------------------------------- in (5)
    _entity(
        "AADHAAR", "in", ("DPDP-2t", "AADHAAR-2a", "AADHAAR-4.2"), NOT_GENERATABLE,
        "No reserved test range exists, and the corrected position is that there is no "
        "safe value at all. 2222 2222 2222 is folklore and is NOT Verhoeff-valid. The "
        "repeated-digit strings 333333333333, 666666666666 and 999999999999 DO satisfy "
        "Verhoeff and the first-digit 2-9 rule, but the scheme also excludes "
        "palindromes, so all three are checksum-valid and scheme-INVALID. The canonical "
        "checksum- and scheme-valid value 234567890124 is not obviously synthetic, and "
        "with no reserved range it could be someone's -- Aadhaar Act s. 29(4) forbids "
        "publishing an Aadhaar number, so it must stay out of published artifacts. "
        "Hence not-generatable rather than a value with a caveat.",
        format_rule="12 digits, first digit 2-9, no palindromes (UIDAI spec unverified)",
        checksum="Verhoeff, dihedral D5 (implementation verified against canonical vectors)",
        negative_control="333333333333",
        publishable=False,
        unverified=True,
        collides_with=("CARDPAN", "IPV4", "DESTID"),
    ),
    _entity(
        "INVID", "in", ("AADHAAR-2a",), NOT_GENERATABLE,
        "16-digit virtual id; both the length and the Verhoeff claim are unverified.",
        format_rule="16 digits (unverified)",
        unverified=True,
        collides_with=("CARDPAN", "ITCF"),
    ),
    _entity(
        "INPAN", "in", ("DPDP-2t", "NIST-2.2"), NOT_GENERATABLE,
        "The issuing authority does not publish the check-character algorithm, so no "
        "value can be SHOWN not to be someone's. That is a stronger bar than 'probably "
        "fine' and it is the right one here.",
        format_rule="AAAAA9999A (unverified)",
        unverified=True,
        collides_with=("MBI", "VIN"),
    ),
    _entity(
        "INGSTIN", "in", ("DPDP-2t",), NOT_GENERATABLE,
        "Embeds a PAN, so it inherits INPAN's problem entirely.",
        format_rule="15 chars: 2-digit state + 10-char PAN + entity digit + Z + check",
        unverified=True,
        collides_with=("INPAN",),
    ),
    _entity(
        "INPHONE", "in", ("DPDP-2t", "HIPAA-D"), NOT_GENERATABLE,
        "No TRAI drama or fiction range was found. Every well-formed Indian mobile "
        "number is potentially live.",
        format_rule="10 digits, first digit 6-9 (unverified)",
        unverified=True,
        collides_with=("NHSNUM", "NPI", "USPHONE"),
    ),
    # ----------------------------------------------------------- ca / au / sg (10)
    _entity(
        "CASIN", "ca", ("GDPR-4", "NIST-2.2"), NOT_GENERATABLE,
        "Computed exhaustively: of the ten 9-digit repeated-digit strings, only "
        "000000000 satisfies Luhn, and an all-zero SIN is not issuable. There is no "
        "obviously-synthetic checksum-valid SIN. Saying so beats inventing one.",
        format_rule="9 digits NNN-NNN-NNN",
        checksum="Luhn (implementation verified; that SIN uses it is unverified)",
        unverified=True,
        collides_with=("SSN", "EIN", "ABART", "NLBSN", "AUTFN"),
    ),
    _entity(
        "CAPOST", "ca", ("HIPAA-B",), GENERATABLE,
        "An unassigned forward sortation area rather than K1A 0B1, which is a real "
        "government postal code.",
        format_rule="A9A 9A9, excluding D,F,I,O,Q,U (unverified)",
        synthetic="Z0Z 0Z0",
        unverified=True,
        collides_with=("UKPOST",),
    ),
    _entity(
        "CAHCN", "ca", ("HIPAA-I",), NO_MATCHABLE_FORMAT,
        "Per-province, every province differs. As with USDL, 'Canadian health card' is "
        "not one entity.",
        unverified=True,
        collides_with=("NHSNUM", "NPI"),
    ),
    _entity(
        "AUTFN", "au", ("NIST-2.2",), NOT_GENERATABLE,
        "Same arithmetic result as CASIN -- only 000000000 passes the published "
        "weighting -- compounded by the weighting itself being unverified.",
        format_rule="8 or 9 digits (unverified)",
        unverified=True,
        collides_with=("SSN", "ABART", "CASIN", "NLBSN"),
    ),
    _entity(
        "AUMCARE", "au", ("HIPAA-I",), NOT_GENERATABLE,
        "Weighting unverified.",
        format_rule="10 digits + 1 issue digit, first digit 2-6 (unverified)",
        unverified=True,
        collides_with=("NHSNUM", "CAHCN"),
    ),
    _entity(
        "AUABN", "au", ("GDPR-4",), NOT_GENERATABLE,
        "Mod 89 weighting unverified; a sole trader's ABN is personal data.",
        format_rule="11 digits (unverified)",
        unverified=True,
        collides_with=("DESTID", "PLPESEL"),
    ),
    _entity(
        "AUPHONE", "au", ("HIPAA-D",), NOT_GENERATABLE,
        "ACMA publishes fictitious ranges for film, TV and radio, but acma.gov.au timed "
        "out on three attempts, so the range is unconfirmed and no value is invented.",
        format_rule="04NN NNN NNN mobile (unverified)",
        unverified=True,
    ),
    _entity(
        "SGNRIC", "sg", ("GDPR-4", "DPDP-2t"), NOT_GENERATABLE,
        "ICA does not publish the format. The weighting is self-consistent but has no "
        "vector to check against, so a value generated from it cannot be scored against "
        "a gateway -- a validating detector rejecting it would be indistinguishable "
        "from a miss.",
        format_rule="prefix S/T/F/G/M + 7 digits + check letter (unverified)",
        unverified=True,
        collides_with=("NINO",),
    ),
    _entity(
        "SGUEN", "sg", ("GDPR-4",), NOT_GENERATABLE,
        "Unverified, and the business-form UEN reuses the NRIC shape.",
        format_rule="9 or 10 alphanumerics (unverified)",
        unverified=True,
        collides_with=("SGNRIC",),
    ),
    _entity(
        "SGPHONE", "sg", ("HIPAA-D",), NOT_GENERATABLE,
        "No fictitious range known.",
        format_rule="8 digits, first digit 6, 8 or 9 (unverified)",
        unverified=True,
        collides_with=("ESDNI",),
    ),
)

BY_ID: dict[str, dict[str, Any]] = {entity["id"]: entity for entity in ENTITIES}


def entities_with(disposition: str) -> tuple[dict[str, Any], ...]:
    return tuple(entity for entity in ENTITIES if entity["disposition"] == disposition)


def corpus_entities() -> tuple[dict[str, Any], ...]:
    """The rows that produce cases. Everything else is a declared exclusion."""
    return entities_with(GENERATABLE)


def declared_exclusions() -> tuple[dict[str, Any], ...]:
    """Rows that appear in the manifest with a reason and produce no cases.

    Published, not dropped. The size of this set IS a finding: it is what "derive the
    entity list from external authority" costs once you stop accepting blog-sourced
    format rules and refuse to invent identifiers that could be someone's.
    """
    return tuple(entity for entity in ENTITIES if entity["disposition"] != GENERATABLE)


def counts() -> dict[str, int]:
    tally = {disposition: 0 for disposition in DISPOSITIONS}
    for entity in ENTITIES:
        tally[entity["disposition"]] += 1
    tally["total"] = len(ENTITIES)
    tally["unverified"] = sum(1 for entity in ENTITIES if entity["unverified"])
    return tally


# Quoted from `.llm/research/entity-list.md` §7, NOT recomputed from it. Where these
# disagree with `counts()`, the disagreement is the artifact: see the module docstring.
# §7 counts only the rows its §5 register names explicitly as unsafe; this module also
# records rows whose value is absent because the format is jurisdictional or binary,
# which §7 tallies under its separate "no format a detector could match" heading.
ENTITY_LIST_PUBLISHED_COUNTS = {
    "total": 72,
    "unsafe_to_generate": 26,
    "no_matchable_format": 12,
    "unverified": 36,
    "reserved_test_range": 5,
}

def containment_collisions() -> tuple[tuple[str, str], ...]:
    """Generatable pairs whose normalized values contain one another.

    The leak matcher strips separators before searching, so if one entity's normalized
    value is a substring of another's, a case carrying both reports the shorter one
    leaked whenever the longer one does. `_ipv4_can_produce` in `http_profile` is the
    same problem solved for one pair by construction; this is the general form.

    THE CORPUS BUILDER MUST NOT PLACE BOTH MEMBERS OF A PAIR IN ONE CASE. That is a
    constraint on the covering array, and it cannot be discharged by choosing better
    values: for most of these there is no other safe value to choose.

    The dominant cluster is all zeros, and it is structural rather than careless. The
    only never-assigned ZIP is `00000`; the only routing number that satisfies the 3-7-1
    weighting without belonging to a district is `000000000`; a sort code has no
    checksum so `00-00-00` is the most synthetic value available. "Obviously synthetic"
    and "all zeros" turn out to be nearly the same requirement, so these entities
    collide with each other by construction.

    Returned as (container, contained) pairs, computed from the values rather than
    listed, so a value change cannot leave a stale exclusion behind.
    """
    folded = {
        entity["id"]: _normalize_for_collision(entity["synthetic"])
        for entity in corpus_entities()
    }
    return tuple(
        (container, contained)
        for container, container_value in sorted(folded.items())
        for contained, contained_value in sorted(folded.items())
        if container != contained and contained_value and contained_value in container_value
    )


def _normalize_for_collision(text: str) -> str:
    """The matcher's fold, reimplemented for the one property that matters here.

    Deliberately NOT an import of `http_profile._normalize`: the registry is data and
    must stay importable without pulling in the harness. Kept to the same rule --
    case-fold, drop everything outside [0-9a-z] -- and the test asserts the two agree.
    """
    return "".join(character for character in text.casefold() if character.isascii() and character.isalnum())


COUNT_DELTAS = (
    "entity-list.md §7 says 26 unsafe to generate and 36 rows carrying an UNVERIFIED "
    "cell. This registry derives its own figures from the §3 tables cell by cell -- see "
    "counts() -- and they differ. Both are recorded; neither is silently adjusted to "
    "match the other. Resolving them is a documentation task on entity-list.md, not a "
    "reason to edit the registry until the criterion is settled."
)
