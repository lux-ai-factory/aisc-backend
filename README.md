# A4S Backend

## Prerequisites

- Python 3.12 or higher
- Git
- uv

## Getting started for development

### 1. Clone repo
```
git clone https://github.com/lux-ai-factory/a4s-backend.git
cd a4s-backend
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



## Docker

To run the full application with docker compose set up your file structure as below with the 3 **a4s** repos
- `a4s-backend`
- `a4s-eval`
- `a4s-webapp`

```
├── a4s-backend
│   ├── docker-compose-infra.yml
│   ├── docker-compose.yml
│   ├── .env
│   └── ...
├── a4s-eval 
│   └── ...  
└── a4s-webapp 
    └── ...
```
Then from within the `a4s-backend` folder run:

```
docker compose -f docker-compose-infra.yml -f docker-compose.yml up
```