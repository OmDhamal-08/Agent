"""Agent-readable catalog endpoint.

Exposes GET /api/agent-catalog — a machine-consumable JSON representation
of the merchant's full product catalog, designed for external AI agents
and systems to query programmatically.
"""

from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends

from backend.database import get_db

router = APIRouter(prefix='/api', tags=['catalog'])


@router.get('/agent-catalog')
async def get_agent_catalog(conn: asyncpg.Connection = Depends(get_db)):
    """Machine-readable product catalog for external AI agents.

    Returns the full product catalog in a structured JSON format.
    This endpoint demonstrates the merchant's catalog being discoverable
    and transactable by external AI agents/buyers.
    """
    rows = await conn.fetch(
        'SELECT * FROM products WHERE stock > 0 ORDER BY id'
    )

    products = []
    for row in rows:
        product = {
            'id': row['id'],
            'name': row['name'],
            'price': {
                'amount': float(row['price']),
                'currency': 'INR',
                'display': f"₹{float(row['price']):,.0f}"
            },
            'specs': {
                'ram_gb': row['ram_gb'],
                'gpu': row['gpu'],
                'cpu': row['cpu'],
            },
            'use_cases': list(row['use_case']) if row['use_case'] else [],
            'stock': {
                'available': row['stock'] > 0,
                'quantity': row['stock']
            },
            'category': row['category']
        }
        products.append(product)

    return {
        'catalog': {
            'merchant': 'ShopMind Electronics',
            'version': '1.0',
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'schema_version': '1.0',
            'total_products': len(products),
            'products': products
        },
        '_links': {
            'self': '/api/agent-catalog',
            'search': '/api/chat',
            'documentation': '/api/docs'
        },
        '_meta': {
            'description': 'Machine-readable product catalog for AI agent discovery and querying.',
            'contact': 'merchant@shopmind.ai',
            'supported_actions': [
                'search_products — Filter by budget, use_case, RAM',
                'compare_products — Compare multiple products',
                'add_to_cart — Add product to cart (requires confirmation)',
                'initiate_checkout — Start payment (requires confirmation)'
            ]
        }
    }
