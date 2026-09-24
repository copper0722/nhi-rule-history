from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nhi_rule_history.contracts import (
    canonical_json_bytes,
    sha256_bytes,
    stable_id,
)
from nhi_rule_history.update.pg_queue import (
    UpdateQueueError,
    _prepare_poll_load,
)
from nhi_rule_history.update.poll import (
    RSS_PARSER_VERSION,
    RSS_V2_PARSER_VERSION,
    observe_feed,
)
from nhi_rule_history.update.rss import (
    RSS_V2_CLASSIFIER_VERSION,
    OfficialResponse,
    parse_rss,
)

FEED_URL = "https://www.nhi.gov.tw/ch/rss-3258-1.xml"

# One 2.0.0 drug-rule title and the 2026-09-15 section 4.2 title shape, which
# carries a clause code and 給付規定 but no 藥品.
FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>NHI classifier version fixture</title>
<item><title>修訂全民健康保險藥品給付規定</title>
<link>https://www.nhi.gov.tw/ch/cp-rule-3258-1.html</link>
<guid>rule-1</guid><description>藥品給付規定</description>
<pubDate>Mon, 14 Sep 2026 08:00:00 +0800</pubDate></item>
<item><title>公告修訂4.2.血液代用製劑及血液成分製劑之給付規定。</title>
<link>https://www.nhi.gov.tw/ch/cp-section-3258-1.html</link>
<guid>section-4-2</guid><description>健保藥品與特材</description>
<pubDate>Tue, 15 Sep 2026 08:00:00 +0800</pubDate></item>
</channel></rss>""".encode()


def feed_response() -> OfficialResponse:
    return OfficialResponse(
        request_url=FEED_URL,
        final_url=FEED_URL,
        status_code=200,
        headers={"content-type": "application/rss+xml"},
        body=FEED,
        observed_at="2026-09-16T06:12:40+00:00",
    )


class PollClassifierVersionTests(unittest.TestCase):
    def test_v2_poll_keeps_v2_selection_while_new_polls_use_v3(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            poll = observe_feed(
                Path(temporary),
                response=feed_response(),
                observed_guids=[],
                previous_item_count=None,
            )
            manifest_path = poll.path / "manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            self.assertEqual(manifest["parser_version"], RSS_PARSER_VERSION)
            current = _prepare_poll_load(poll.path, owner_key="fixture-owner")
            self.assertEqual(
                current.new_likely_guids, frozenset({"rule-1", "section-4-2"})
            )

            v2_items = [
                item.as_dict(classifier_version=RSS_V2_CLASSIFIER_VERSION)
                for item in parse_rss(FEED)
            ]
            self.assertEqual(
                [item["is_likely_drug_rule"] for item in v2_items], [True, False]
            )
            sequence_sha = sha256_bytes(canonical_json_bytes(v2_items))

            def seal_as_v2(new_likely: list[str]) -> None:
                manifest["parser_version"] = RSS_V2_PARSER_VERSION
                manifest["items"] = v2_items
                manifest["item_sequence_sha256"] = sequence_sha
                manifest["new_likely_drug_rule_guids"] = new_likely
                manifest["poll_id"] = stable_id(
                    "nhi-rss-poll",
                    manifest["feed_url"],
                    manifest["observed_at"],
                    manifest["feed_artifact_sha256"],
                    sequence_sha,
                    manifest["observed_guid_set_sha256"],
                    str(manifest.get("previous_item_count")),
                    format(float(manifest.get("collapse_ratio")), ".12g"),
                )
                manifest_path.write_bytes(canonical_json_bytes(manifest))

            seal_as_v2(["rule-1"])
            sealed_v2 = _prepare_poll_load(poll.path, owner_key="fixture-owner")
            self.assertEqual(sealed_v2.new_likely_guids, frozenset({"rule-1"}))

            seal_as_v2(["rule-1", "section-4-2"])
            with self.assertRaises(UpdateQueueError):
                _prepare_poll_load(poll.path, owner_key="fixture-owner")


if __name__ == "__main__":
    unittest.main()
