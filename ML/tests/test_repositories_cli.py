import json
from pathlib import Path

from ML.cli import run
from ML.repositories import FileResumeRepository, iter_job_records, job_fields


RUNTIME = Path(__file__).parent / "runtime"


def test_resume_and_job_file_adapters():
    resume = RUNTIME / "resume.json"
    resume.write_text(json.dumps({"resume": "Senior Python SQL engineer, 6 years of experience"}), encoding="utf-8")
    jobs = RUNTIME / "jobs.jsonl"
    jobs.write_text(
        json.dumps({"job_id": "1", "job_title": "Senior Engineer", "job_text": "Python SQL, 4 years of experience"}) + "\n",
        encoding="utf-8",
    )
    assert "Python" in FileResumeRepository(resume).load_text()
    record = next(iter_job_records(jobs))
    assert job_fields(record) == ("1", "Senior Engineer", "Python SQL, 4 years of experience")


def test_cli_writes_selected_json_report(monkeypatch):
    import ML.cli as cli
    import spacy
    from ML.extraction import SpacyProfileExtractor

    monkeypatch.setattr(cli, "SpacyProfileExtractor", lambda: SpacyProfileExtractor(spacy.blank("pt")))
    resume = RUNTIME / "resume.txt"
    resume.write_text("Senior Python SQL engineer with 8 years of experience", encoding="utf-8")
    jobs = RUNTIME / "jobs.json"
    jobs.write_text(json.dumps([
        {"id": "good", "title": "Senior Python Engineer", "description": "Python SQL, 5 years of experience"},
        {"id": "bad", "title": "Mechanical Engineer", "description": "Mechanical engineering and AutoCAD"},
    ]), encoding="utf-8")
    output = RUNTIME / "report.json"
    assert run(resume, jobs, output, threshold=60) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert [item["id"] for item in report] == ["good"]
    assert report[0]["compatibility_score"] >= 60
