from pathlib import Path
from jinja2 import Template
from db import connect

HTML = """
<h1>Daily Tech Trend</h1>
<ul>
{% for t in topics %}
  <li>{{ t }}</li>
{% endfor %}
</ul>
"""

def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
  SELECT COALESCE(title_ja, title)
  FROM topics
  ORDER BY id DESC
  LIMIT 50
""")

    topics = [r[0] for r in cur.fetchall()]

    site = Path("docs")
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text(
        Template(HTML).render(topics=topics),
        encoding="utf-8"
    )

if __name__ == "__main__":
    main()
