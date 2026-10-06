import screentime_categories as cats


def test_subdomain_of_multi_label_suffix_site_is_categorized():
    assert cats._DOMAIN_CATEGORIES["10000games.co.uk"] == "Games"
    assert cats.categorize_domain("news.10000games.co.uk") == "Games"
    assert cats.categorize_domain("a.b.1001jogos.com.br") == cats._DOMAIN_CATEGORIES["1001jogos.com.br"]


def test_trailing_dot_fqdn_is_categorized():
    assert cats.categorize_domain("youtube.com.") == cats.categorize_domain("youtube.com") != "Other"


def test_existing_behaviour_kept():
    assert cats.categorize_domain("m.youtube.com") == cats.categorize_domain("youtube.com")
    assert cats.categorize_domain("unknown-example.invalid") == "Other"
    assert cats.categorize_domain("") == "Other"
