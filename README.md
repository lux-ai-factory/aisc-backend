# Demo Django Project

Headless [Django](https://www.djangoproject.com/) project built with [django-allauth](https://docs.allauth.org/en/latest/) and [Django Ninja](https://django-ninja.dev/).

Uses [uv](https://docs.astral.sh/uv/) for environment and dependency management.

> ⚠️ **Important**: Intended as a **starting point for new projects**, not as a production-ready application.

## Features

- **Headless Django setup** with [Django Ninja](https://django-ninja.dev/) as the API layer  
- **User management flows** powered by [django-allauth](https://django-allauth.readthedocs.io/)  
  - Signup, login, logout, password reset, email verification etc
- **Dual authentication support**  
  - Access API endpoints with **standard Django session cookies**  
  - Or authenticate via **JWT tokens**  
- **Example application model: `Item`**  
  - Demonstrates some CRUD operations  
  - Basic permission checks
- **Auto-generated OpenAPI/Swagger docs**


## Prerequisites

- Python 3.12 or higher
- Git
- uv

## Getting started

### 1. Clone repo
```
git clone https://gitlab.com/uniluxembourg/snt/tto/r2s/team/sblevins/demo-django-project.git
cd demo-django-project
```

### 2. Install dependencies
```
uv sync
```

### 3. Run migrations
```
uv run python manage.py migrate
```

### 4. Create admin user
```
uv run python manage.py createsuperuser
```

### 5. Collect static files
```
uv run python manage.py collectstatic  
```

### 6. Running tests
```
uv run python manage.py test
```


## Running the app

```
uv run python manage.py runserver
```

## Admin dashboard

To access the admin dashboard run the app then go to:
- http://127.0.0.1:8000/admin

## API Docs

To access the OpenAPI docs run the app then go to:

- allauth endpoints
  - http://127.0.0.1:8000/_allauth/openapi.html
- Django Ninja endpoints
  - http://127.0.0.1:8000/api/docs

## Authentication

This project integrates **django-allauth (headless)** which provides two groups of endpoints:

- **Browser endpoints** (`/_allauth/browser/...`):
  - Intended for browser-based clients on same origin as backend
  - Authenticate via standard Django session cookies
  - Login sets a Django session cookie via response header (`Set-Cookie: sessionid=...`)

- **App endpoints** (`/_allauth/app/...`):
  - Intended for API/mobile clients
  - Authenticate via JWT
  - Login returns `access_token` and `refresh_token` in response body JSON
  - Use custom token refresh endpoint `/api/v1/tokens/refresh` to get new access token
