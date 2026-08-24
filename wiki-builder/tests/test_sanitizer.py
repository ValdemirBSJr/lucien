from app.mkdocs_hook import sanitize_page_content


def test_sanitizador_remove_script_evento_e_javascript() -> None:
    unsafe = (
        '<h2 id="passo" onclick="roubar()">Passo</h2>'
        '<script>alert(1)</script>'
        '<a href="javascript:roubar()" onmouseover="roubar()">link</a>'
        '<pre><code class="language-bash">echo ok</code></pre>'
    )

    clean = sanitize_page_content(unsafe)

    assert "<script" not in clean
    assert "onclick" not in clean
    assert "onmouseover" not in clean
    assert "javascript:" not in clean
    assert '<h2 id="passo">Passo</h2>' in clean
    assert 'class="language-bash"' in clean

