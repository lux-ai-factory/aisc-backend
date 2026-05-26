# Simplified Gitflow Guide

This document describes the current simplified branching strategy for all `AISC` projects.

> This guide is intentionally lightweight and subject to change as the project and deployment process evolve.

## Goals

The current branching strategy is designed to support:

- easier day-to-day development
- clearer organization of work
- a simple and predictable merge process

Because there is **no long-lived deployment environment yet**, this workflow focuses more on developer ease than on release complexity.

## Main Branches

### `main` / `master`

- This branch is **protected**
- It should always be in a **deployable state**
- Releases from this branch should be **tagged** with a sematic version number
- Only code that is ready for release should be merged here

> Note: The repository may use either `main` or `master`. The protected primary branch should follow these rules.

### `dev` / `develop`

- This branch is **protected**
- It acts as the main integration branch for ongoing work
- **Feature** and **fix** branches should be merged into this branch
- When the team is ready for a release, `dev` / `develop` is merged into `main` / `master`

## Short-Lived Branches

### Feature branches

Feature branches should:

- be created from `dev` / `develop`
- contain work for new functionality
- be merged back into `dev` / `develop` through a pull request

### Fix branches

Fix branches should:

- be created from `dev` / `develop`
- contain bug fixes or small corrections
- be merged back into `dev` / `develop` through a pull request

## Branch Naming

There is currently **no strict naming convention** for feature or fix branches.

That said, branch names should still be reasonably clear and descriptive. For example:

- `feature/user-import`
- `fix/login-timeout`
- `feature/add-audit-logging`

A stricter naming standard may be introduced later.

## Pull Requests

All feature and fix branches should be merged into `dev` / `develop` using a pull request.

At a minimum, pull requests should aim to:

- keep changes focused
- be understandable to reviewers
- avoid merging incomplete or broken work

Additional PR requirements may be defined later.

## Releases

When the code in `dev` / `develop` is considered ready:

1. Merge `dev` / `develop` into `main` / `master`
2. Tag the release on `main` / `master`

This keeps the primary branch stable and release-oriented.

## Automation / Actions

There will likely be GitHub Actions or other automated steps at various points in this workflow, such as:

- pull request validation
- test execution
- linting or quality checks
- release-related actions

These are **not fully defined yet** and will be documented once agreed.

## Summary

In short:

- `main` / `master` is protected, tagged, and always deployable
- `dev` / `develop` is protected and used for integrating ongoing work
- feature and fix branches start from `dev` / `develop`
- feature and fix branches merge back into `dev` / `develop` via pull request
- releases happen by merging `dev` / `develop` into `main` / `master`

## Future Changes

This guide is a starting point and is expected to evolve as the project matures, especially once deployment workflows and automation are better defined.