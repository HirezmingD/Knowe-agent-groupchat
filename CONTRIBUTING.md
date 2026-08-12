# Contributing

**简体中文版：[CONTRIBUTING.zh-CN.md](./CONTRIBUTING.zh-CN.md)**

Thanks for your interest in contributing to Knowe! This is a small project.

## Ways to contribute

- **Report bugs**: open an issue with steps to reproduce
- **Suggest features**: open an issue describing the problem you're solving
- **Fix bugs / add features**: fork, branch, code, and open a pull request

## Development setup

See [TECH.md](./TECH.md) for architecture and local development:

```bash
npm install
pip install -r backend/requirements.txt
npm run electron:dev
```

## Before submitting a PR

1. Run checks: `npm run typecheck && npm run lint && npm run test`
2. Run backend tests: `pytest backend/tests/` (from `backend/`)
3. Keep changes focused — one PR per concern
4. Update docs if behavior changes (user manual lives in `docs/`)

## Code style

- Frontend: TypeScript strict, ESLint rules enforced (`npm run lint`)
- Backend: Python, follows existing module conventions
- Keep it simple — this project values minimal, readable code over clever abstractions

## License

By contributing, you agree that your contributions are licensed under the [MIT](./LICENSE) license.
