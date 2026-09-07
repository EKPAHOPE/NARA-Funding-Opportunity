import unittest
from datetime import date

from funding_scraper import Opportunity, classify_opportunity, filter_opportunities, parse_listing


class FundingScraperTests(unittest.TestCase):
    def test_parse_nihr_listing(self):
        html = """
        <main>
          <article class="funding-card">
            <h3><a href="/funding/mental-health-addiction-study/2026001">Mental health and addiction interventions</a></h3>
            <p>Research into effective interventions for substance misuse and depression.</p>
            <p>Opening date: 1 September 2026 at 1:00 pm</p>
            <p>Closing date: 30 November 2026 at 1:00 pm</p>
            <p>Status: Open</p>
          </article>
        </main>
        """
        items = parse_listing(html, "https://www.nihr.ac.uk/funding-opportunities", "NIHR")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].opening_date, date(2026, 9, 1))
        self.assertEqual(items[0].deadline, date(2026, 11, 30))
        self.assertIn("/funding/mental-health-addiction-study/2026001", items[0].url)

    def test_parse_british_academy_listing(self):
        html = """
        <section class="card">
          <h3><a href="/funding/interdisciplinary-health-awards/">Interdisciplinary Health Awards</a></h3>
          <p>Support for interdisciplinary research into wellbeing and society.</p>
          <p>Closing date: 15 September 2026</p>
        </section>
        """
        items = parse_listing(html, "https://www.thebritishacademy.ac.uk/funding/", "The British Academy")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].deadline, date(2026, 9, 15))

    def test_parse_wellcome_listing(self):
        html = """
        <article class="scheme-card">
          <h3><a href="/research-funding/schemes/mental-health-award">
            Mental Health Award: early intervention
          </a></h3>
          <p>Support for research that improves mental health outcomes for young people.</p>
          <p>Applications close: 20 October 2026</p>
        </article>
        """
        items = parse_listing(html, "https://wellcome.org/research-funding/schemes", "Wellcome")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].deadline, date(2026, 10, 20))
        self.assertEqual(items[0].funder, "Wellcome")

    def test_parse_ukri_listing(self):
        html = """
        <article class="opportunity">
          <h3><a href="/opportunity/addiction-research-programme/">
            Addiction research programme
          </a></h3>
          <p>Apply for funding to study alcohol harm and substance misuse.</p>
          <p>Opportunity status: Upcoming</p>
          <p>Opening date: 10 September 2026</p>
          <p>Closing date: 19 November 2026</p>
        </article>
        """
        items = parse_listing(html, "https://www.ukri.org/opportunity/", "UKRI")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].opening_date, date(2026, 9, 10))
        self.assertEqual(items[0].status, "Opening soon")
        self.assertEqual(items[0].funder, "UK Research and Innovation")

    def test_topic_classification(self):
        item = Opportunity(
            source="NIHR",
            funder="NIHR",
            title="Alcohol harm and mental health",
            url="https://www.nihr.ac.uk/funding/example/1",
            summary="A study of addiction, anxiety and substance misuse.",
        )
        classify_opportunity(item)
        self.assertIn("Alcohol", item.matched_topics)
        self.assertIn("Addiction", item.matched_topics)
        self.assertIn("Mental health", item.matched_topics)

    def test_custom_filter(self):
        item = Opportunity(
            source="NIHR",
            funder="NIHR",
            title="Gambling harms",
            url="https://www.nihr.ac.uk/funding/example/2",
            summary="Research into betting behaviour.",
            status="Open",
            opening_date=date.today(),
        )
        classify_opportunity(item)
        result = filter_opportunities(
            [item],
            topics=[],
            custom_query="gambling",
            include_broad=False,
            statuses=["Open"],
            date_filter="Opened in the last 7 days",
        )
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
