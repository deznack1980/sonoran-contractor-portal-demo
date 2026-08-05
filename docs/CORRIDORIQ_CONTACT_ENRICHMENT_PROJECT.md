# CorridorIQ Contact Enrichment Project

## Project objective

CorridorIQ converts Arizona municipal permit records into contractor and project intelligence.
This contact-enrichment project adds verified business contact information to contractor profiles
so sales users can move from a permit signal to an informed outreach action without leaving the
CorridorIQ portal.

The current production dataset contains:

- 109,184 permit-backed projects
- 12,548 canonical company identities
- 10,822 companies with contractor-linked projects
- no verified phone, email, website, or named-contact records at the start of this project

## What Perplexity receives

Each numbered CSV batch contains up to 100 companies needing contact enrichment. Rows are ordered
by CorridorIQ opportunity priority, active-project count, total-project count, and company name.

Identity/context columns supplied by CorridorIQ must remain unchanged:

- company_id
- company_name
- legal_name
- license_number
- city
- state
- company_type
- priority_tier
- priority_score
- total_projects
- active_projects
- latest_activity_date
- permit_jurisdictions
- known_aliases

## What Perplexity must research

For each company, find as much verified business information as is publicly available:

- primary business phone
- public business email
- official website
- complete business address
- owner, president, purchasing manager, estimator, project manager, or another useful decision-maker
- that person's public business phone and email, when verifiable
- contractor specialty
- service territory
- license status
- other commercially useful and verifiable information

## Identity rules

1. Preserve company_id exactly. It is CorridorIQ's immutable database key.
2. Match using company name, legal name, license number, city/state, aliases, and permit
   jurisdictions together.
3. Do not merge similarly named businesses.
4. Do not substitute a national parent, franchise, dealer, or branch unless the supplied identity
   evidence establishes that it is the same legal or operating entity.
5. If identity is uncertain, mark the row Ambiguous and explain the conflict.
6. Never guess phone numbers, emails, websites, names, titles, or addresses.
7. Never infer an email pattern unless that exact email is publicly displayed by a reliable source.

## Source priority

Prefer sources in this order:

1. Official company website
2. Arizona Registrar of Contractors or another official licensing authority
3. Arizona Corporation Commission or an official state corporate registry
4. Municipal permit or procurement records
5. Manufacturer, dealer, distributor, or trade-association directory
6. Established business profile or professional profile that clearly matches the entity

Search-result snippets alone are not sufficient evidence when the underlying page can be verified.

## Evidence requirements

- Every entered contact value must be supported by at least one URL in source_urls.
- Separate multiple source URLs with semicolons.
- Use research_confidence: High, Medium, or Low.
- Use research_status: Found, Partial, Not Found, or Ambiguous.
- Use researched_at in YYYY-MM-DD format.
- Put important identity caveats or commercially useful facts in other_useful_information.

## Output contract

Return CSV with the exact original column order. Preserve every input row, including Not Found and
Ambiguous rows. Do not return a prose-only summary.

Enrichment columns:

- research_phone
- research_email
- research_website
- research_address
- primary_contact_name
- primary_contact_title
- primary_contact_phone
- primary_contact_email
- other_useful_information
- source_urls
- research_confidence
- research_status
- researched_at

## Import safeguards

Completed batches are not loaded directly into production. CorridorIQ first performs a dry run that:

- verifies company_id exists
- compares the supplied company name with canonical names and aliases
- validates phones, emails, and URLs
- requires evidence URLs for contact data
- rejects contact data on Not Found or Ambiguous rows
- produces a rejection report for human review

An applied import creates a SQLite backup, fills missing company contact fields without overwriting
existing verified data by default, deduplicates named contacts, and writes an identity audit record.

## Definition of success

A researched row is successful only when it improves the company's verified outreach readiness
without weakening identity integrity. A correct Not Found or Ambiguous result is more valuable than
a fabricated or mismatched contact.
