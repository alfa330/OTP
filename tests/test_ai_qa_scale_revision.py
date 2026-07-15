import unittest

from call_qa.rag import knowledge


class ScaleCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        self.row = None
        if "SELECT id FROM qa_scale_revisions" in compact:
            self.row = self.connection.revisions.get((int(params[0]), str(params[1])))
            self.row = (self.row[0],) if self.row else None
        elif "SELECT direction_id FROM qa_criterion_registry" in compact:
            direction = self.connection.registry.get(str(params[0]))
            self.row = (direction,) if direction is not None else None
        elif compact.startswith("INSERT INTO qa_criterion_registry"):
            self.connection.registry[str(params[0])] = int(params[1])
        elif "SELECT COALESCE(MAX(scale_revision),0)+1" in compact:
            existing = [row[1] for (direction, _hash), row in self.connection.revisions.items()
                        if direction == int(params[0])]
            self.row = (max(existing, default=0) + 1,)
        elif compact.startswith("INSERT INTO qa_scale_revisions"):
            direction, revision, revision_hash = int(params[0]), int(params[1]), str(params[2])
            row_id = self.connection.next_id
            self.connection.next_id += 1
            self.connection.revisions[(direction, revision_hash)] = (row_id, revision)
            self.connection.manifests[row_id] = params[3].adapted
            self.row = (row_id,)

    def fetchone(self):
        return self.row


class ScaleConnection:
    def __init__(self):
        self.revisions = {}
        self.registry = {}
        self.manifests = {}
        self.next_id = 1

    def cursor(self):
        return ScaleCursor(self)


def criterion(source):
    return {
        "idx": 0,
        "criterion_id": "greeting",
        "name": "Приветствие",
        "description": "Оператор должен поздороваться",
        "weight": 5,
        "is_critical": False,
        "deficiency": None,
        "eval_source": source,
    }


class ScaleRevisionIdentityTests(unittest.TestCase):
    def test_source_change_creates_new_immutable_revision(self):
        conn = ScaleConnection()
        structural_hash = "a" * 64

        transcript_revision = knowledge.sync_scale_revision(
            conn, direction_id=72, scale_hash=structural_hash,
            criteria=[criterion("transcript")],
        )
        manual_revision = knowledge.sync_scale_revision(
            conn, direction_id=72, scale_hash=structural_hash,
            criteria=[criterion("manual")],
        )
        reused_manual_revision = knowledge.sync_scale_revision(
            conn, direction_id=72, scale_hash=structural_hash,
            criteria=[criterion("manual")],
        )

        self.assertNotEqual(transcript_revision, manual_revision)
        self.assertEqual(manual_revision, reused_manual_revision)
        self.assertEqual(len(conn.revisions), 2)
        self.assertEqual(conn.manifests[transcript_revision][0]["eval_source"], "transcript")
        self.assertEqual(conn.manifests[manual_revision][0]["eval_source"], "manual")

    def test_revision_fingerprint_includes_source(self):
        transcript = knowledge._criterion_manifest([criterion("transcript")])
        manual = knowledge._criterion_manifest([criterion("manual")])
        self.assertNotEqual(
            knowledge.scale_revision_fingerprint(
                direction_id=72, scale_hash="a" * 64, criteria_manifest=transcript),
            knowledge.scale_revision_fingerprint(
                direction_id=72, scale_hash="a" * 64, criteria_manifest=manual),
        )


if __name__ == "__main__":
    unittest.main()
