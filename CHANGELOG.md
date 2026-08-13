# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Django integration over the `langsys` base SDK: a `{% t %}` template tag and `|t`
  filter, a `t()` helper for view/business code, and request-locale middleware that resolves
  the language from the query string, a cookie, or `Accept-Language` — persisting an explicit
  choice to a cookie and registering phrases discovered while rendering (write key) after the
  response.
- A request-safe shared client whose locale comes from a `contextvars` context variable, so a
  single instance serves concurrent requests, each in its own language.
