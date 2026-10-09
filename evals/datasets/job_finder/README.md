# Job Finder evaluation set

`labelled.jsonl` is the starter set. Lead rows measure URL recall, JD rows require the known
full-JD URL, wrong-job rows must return no match, and blocked-url rows exercise private-network and job-board
fetch refusal. Replace the `.example` lead/JD rows with labelled real jobs before the final live gate; keep the
security rows permanently.
