#!/usr/bin/env python3
"""
Flask Backend for Stripe Rebilling Dashboard
Uses the same working logic as charge_all_customers.py
"""

from flask import Flask, request, jsonify
import stripe
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

# ============================================================
# CORS CONFIGURATION
# ============================================================

@app.after_request
def after_request(response):
    """Add CORS headers to every response"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


# ============================================================
# API ENDPOINTS
# ============================================================

@app.route('/health', methods=['GET', 'OPTIONS'])
def health():
    """Health check endpoint"""
    if request.method == 'OPTIONS':
        return '', 204
    
    return jsonify({
        'status': 'ok',
        'message': 'Backend is running',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/get-business-info', methods=['POST', 'OPTIONS'])
def get_business_info():
    """Get Stripe account/business information"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        # Get account information
        account = stripe.Account.retrieve()
        
        # Get balance
        balance = stripe.Balance.retrieve()
        available_balance = sum([bal['amount'] / 100 for bal in balance.available]) if balance.available else 0
        pending_balance = sum([bal['amount'] / 100 for bal in balance.pending]) if balance.pending else 0
        
        # Extract account details
        business_name = getattr(account, 'business_profile', {}).get('name', 'N/A') if hasattr(account, 'business_profile') else 'N/A'
        country = getattr(account, 'country', 'N/A')
        email = getattr(account, 'email', 'N/A')
        account_type = getattr(account, 'type', 'N/A')
        charges_enabled = getattr(account, 'charges_enabled', False)
        payouts_enabled = getattr(account, 'payouts_enabled', False)
        default_currency = getattr(account, 'default_currency', 'usd').upper()
        
        # Get payout schedule information
        payout_schedule = {}
        if hasattr(account, 'settings') and hasattr(account.settings, 'payouts'):
            payout_settings = account.settings.payouts
            payout_schedule = {
                'interval': getattr(payout_settings.schedule, 'interval', 'manual'),
                'delay_days': getattr(payout_settings.schedule, 'delay_days', 0),
                'weekly_anchor': getattr(payout_settings.schedule, 'weekly_anchor', None),
                'monthly_anchor': getattr(payout_settings.schedule, 'monthly_anchor', None),
            }
        
        # Check if instant payouts are available
        instant_available = False
        
        # First, try to get all capabilities as dict
        capabilities_dict = {}
        if hasattr(account, 'capabilities'):
            capabilities = account.capabilities
            
            # Convert to dict for easier checking
            if isinstance(capabilities, dict):
                capabilities_dict = capabilities
            else:
                # It's a Stripe object, convert to dict
                try:
                    capabilities_dict = dict(capabilities)
                except:
                    # Try to access as object attributes
                    if hasattr(capabilities, '__dict__'):
                        capabilities_dict = capabilities.__dict__
        
        print(f"🔍 Debug - Instant Payouts Check:")
        print(f"   - Country: {country}")
        print(f"   - Payouts enabled: {payouts_enabled}")
        print(f"   - Charges enabled: {charges_enabled}")
        print(f"   - Has capabilities: {hasattr(account, 'capabilities')}")
        
        # Try to get raw capabilities
        if hasattr(account, 'capabilities'):
            print(f"   - Raw capabilities object type: {type(account.capabilities)}")
            print(f"   - Raw capabilities dir: {[attr for attr in dir(account.capabilities) if not attr.startswith('_')]}")
            
        print(f"   - Capabilities dict keys: {list(capabilities_dict.keys()) if capabilities_dict else 'None'}")
        print(f"   - Full capabilities dict: {capabilities_dict}")
        
        # Check for instant_payouts capability - ONLY use real Stripe data
        if 'instant_payouts' in capabilities_dict:
            instant_status = capabilities_dict.get('instant_payouts', 'inactive')
            instant_available = instant_status == 'active'
            print(f"   ✓ instant_payouts found: {instant_status} -> {instant_available}")
        else:
            print(f"   ✗ instant_payouts NOT in capabilities")
            instant_available = False
        
        print(f"   - Final instant_available: {instant_available}")
        
        # Get account requirements/tasks with more detail
        account_tasks = {
            'currently_due': [],
            'eventually_due': [],
            'past_due': [],
            'pending_verification': [],
            'disabled_reason': None,
            'details_submitted': False,
            'payouts_enabled_status': payouts_enabled,
            'charges_enabled_status': charges_enabled
        }
        
        # Common verification fields to check
        all_possible_tasks = [
            'business_profile.mcc',
            'business_profile.url',
            'business_type',
            'external_account',
            'individual.id_number',
            'individual.ssn_last_4',
            'individual.verification.document',
            'individual.address.line1',
            'individual.address.city',
            'individual.address.postal_code',
            'individual.dob.day',
            'individual.dob.month',
            'individual.dob.year',
            'individual.email',
            'individual.first_name',
            'individual.last_name',
            'individual.phone',
            'tos_acceptance.date',
            'tos_acceptance.ip',
            'company.name',
            'company.tax_id',
            'company.address.line1',
            'company.address.city',
            'company.address.postal_code',
            'company.verification.document',
            'representative.first_name',
            'representative.last_name'
        ]
        
        if hasattr(account, 'requirements'):
            requirements = account.requirements
            account_tasks['currently_due'] = list(getattr(requirements, 'currently_due', [])) if hasattr(requirements, 'currently_due') else []
            account_tasks['eventually_due'] = list(getattr(requirements, 'eventually_due', [])) if hasattr(requirements, 'eventually_due') else []
            account_tasks['past_due'] = list(getattr(requirements, 'past_due', [])) if hasattr(requirements, 'past_due') else []
            account_tasks['pending_verification'] = list(getattr(requirements, 'pending_verification', [])) if hasattr(requirements, 'pending_verification') else []
            account_tasks['disabled_reason'] = getattr(requirements, 'disabled_reason', None)
        
        # Check if basic details have been submitted
        if hasattr(account, 'details_submitted'):
            account_tasks['details_submitted'] = getattr(account, 'details_submitted', False)
        
        # Calculate completed tasks (fields not in any due list)
        all_due_tasks = set(account_tasks['currently_due'] + account_tasks['eventually_due'] + account_tasks['past_due'])
        account_tasks['completed_tasks'] = [task for task in all_possible_tasks if task not in all_due_tasks]
        
        # Build human-readable task messages like Stripe Dashboard
        account_tasks['dashboard_tasks'] = {
            'active': [],
            'completed': []
        }
        
        # Active tasks from Stripe Dashboard
        if not charges_enabled or not payouts_enabled:
            account_tasks['dashboard_tasks']['active'].append({
                'message': 'Charges and payouts are paused',
                'type': 'critical'
            })
        
        if account_tasks['past_due']:
            account_tasks['dashboard_tasks']['active'].append({
                'message': 'Provide past due information',
                'type': 'critical'
            })
        
        if account_tasks['currently_due']:
            account_tasks['dashboard_tasks']['active'].append({
                'message': 'Provide additional information',
                'type': 'active'
            })
            
            # Check for specific requirements
            if 'business_profile.url' in account_tasks['currently_due']:
                account_tasks['dashboard_tasks']['active'].append({
                    'message': 'Update your business website',
                    'type': 'active'
                })
            
            if 'external_account' in account_tasks['currently_due']:
                account_tasks['dashboard_tasks']['active'].append({
                    'message': 'Add bank account for payouts',
                    'type': 'active'
                })
        
        if account_tasks['pending_verification']:
            account_tasks['dashboard_tasks']['active'].append({
                'message': 'Documents under review by Stripe',
                'type': 'pending'
            })
        
        # Completed tasks
        if charges_enabled and payouts_enabled:
            account_tasks['dashboard_tasks']['completed'].append({
                'message': 'Account activated for payments and payouts',
                'date': 'Active'
            })
        
        if account_tasks['details_submitted']:
            account_tasks['dashboard_tasks']['completed'].append({
                'message': 'Business details submitted',
                'date': 'Completed'
            })
        
        if 'external_account' not in all_due_tasks:
            account_tasks['dashboard_tasks']['completed'].append({
                'message': 'Bank account connected',
                'date': 'Connected'
            })
        
        if 'business_profile.url' not in all_due_tasks:
            account_tasks['dashboard_tasks']['completed'].append({
                'message': 'Business website provided',
                'date': 'Verified'
            })
        
        print(f"📋 Account Status Debug:")
        print(f"   - Active Tasks: {len(account_tasks['dashboard_tasks']['active'])}")
        for task in account_tasks['dashboard_tasks']['active']:
            print(f"     • {task['message']} ({task['type']})")
        print(f"   - Completed Tasks: {len(account_tasks['dashboard_tasks']['completed'])}")
        for task in account_tasks['dashboard_tasks']['completed']:
            print(f"     ✓ {task['message']}")
        print(f"   - Charges Enabled: {charges_enabled}")
        print(f"   - Payouts Enabled: {payouts_enabled}")
        
        return jsonify({
            'success': True,
            'business_name': business_name,
            'country': country,
            'email': email,
            'account_type': account_type,
            'charges_enabled': charges_enabled,
            'payouts_enabled': payouts_enabled,
            'default_currency': default_currency,
            'available_balance': round(available_balance, 2),
            'pending_balance': round(pending_balance, 2),
            'payout_schedule': payout_schedule,
            'instant_payouts_available': instant_available,
            'debug_capabilities': capabilities_dict,
            'account_tasks': account_tasks
        })
    
    except Exception as e:
        print(f"❌ Error fetching business info: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/validate-key', methods=['POST', 'OPTIONS'])
def validate_key():
    """Validate Stripe API key"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        # Test the API key
        stripe.api_key = api_key
        stripe.Customer.list(limit=1)
        
        # Determine mode
        mode = 'live' if api_key.startswith('sk_live_') else 'test'
        
        return jsonify({
            'success': True,
            'mode': mode
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/check-customers', methods=['POST', 'OPTIONS'])
def check_customers():
    """Detailed customer diagnostic - like check_customers.py"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        # Get all customers
        customers = stripe.Customer.list(limit=100)
        customer_list = list(customers.auto_paging_iter())
        
        customers_with_pm = 0
        customers_with_source = 0
        customers_with_invoice_settings = 0
        customer_details = []
        
        for customer in customer_list:
            # Check for PaymentMethod (new way)
            payment_methods = stripe.PaymentMethod.list(
                customer=customer.id,
                type='card',
                limit=1
            )
            has_pm = len(payment_methods.data) > 0
            if has_pm:
                customers_with_pm += 1
            
            # Check for default source (old way)
            has_source = bool(customer.default_source)
            if has_source:
                customers_with_source += 1
            
            # Check invoice settings default payment method
            has_invoice_pm = bool(customer.invoice_settings and customer.invoice_settings.default_payment_method)
            if has_invoice_pm:
                customers_with_invoice_settings += 1
            
            # Determine if chargeable
            chargeable = has_pm or has_source or has_invoice_pm
            
            customer_details.append({
                'id': customer.id,
                'email': customer.email or 'No email',
                'name': customer.name or 'No name',
                'created': customer.created,
                'hasPaymentMethod': has_pm,
                'hasSource': has_source,
                'hasInvoicePM': has_invoice_pm,
                'chargeable': chargeable
            })
        
        chargeable = max(customers_with_pm, customers_with_source, customers_with_invoice_settings)
        
        return jsonify({
            'success': True,
            'total': len(customer_list),
            'withPaymentMethod': customers_with_pm,
            'withSource': customers_with_source,
            'withInvoicePM': customers_with_invoice_settings,
            'chargeable': chargeable,
            'customers': customer_details
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


def check_customer_payment_method(customer):
    """Helper function to check if customer has payment method - OPTIMIZED for speed"""
    try:
        # FASTEST: Check invoice settings first (no API call needed!)
        if customer.invoice_settings and customer.invoice_settings.default_payment_method:
            return True
        
        # FAST: Check for default source (no extra API call)
        if customer.default_source:
            return True
        
        # SLOWER: Check for PaymentMethod (requires API call)
        # Only do this if the above checks failed
        payment_methods = stripe.PaymentMethod.list(
            customer=customer.id,
            type='card',
            limit=1
        )
        if len(payment_methods.data) > 0:
            return True
        
        return False
    except:
        return False


@app.route('/get-customers-fast', methods=['POST', 'OPTIONS'])
def get_customers_fast():
    """Get ONLY customer count (super fast - no payment method checking)"""
    
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        # Get all customers (fast - no payment method checking)
        customers = stripe.Customer.list(limit=100)
        customer_list = list(customers.auto_paging_iter())
        
        return jsonify({
            'success': True,
            'total': len(customer_list),
            'withPayment': 0,  # Will be updated by the full check
            'fast': True  # Indicates this is a fast response
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/get-customers', methods=['POST', 'OPTIONS'])
def get_customers():
    """Get customer count - OPTIMIZED with parallel processing for 3-5x faster loading"""
    
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        # Get all customers
        customers = stripe.Customer.list(limit=100)
        customer_list = list(customers.auto_paging_iter())
        
        total = len(customer_list)
        chargeable_customers = []
        
        # Use parallel processing to check payment methods (MUCH faster!)
        # Check up to 100 customers at the same time for MAXIMUM speed
        with ThreadPoolExecutor(max_workers=100) as executor:
            # Submit all tasks
            future_to_customer = {
                executor.submit(check_customer_payment_method, customer): customer 
                for customer in customer_list
            }
            
            # Collect results and store chargeable customer IDs
            for future in as_completed(future_to_customer):
                customer = future_to_customer[future]
                if future.result():
                    chargeable_customers.append({
                        'id': customer.id,
                        'email': customer.email or 'No email',
                        'name': customer.name or 'No name'
                    })
        
        return jsonify({
            'success': True,
            'total': total,
            'withPayment': len(chargeable_customers),
            'customers': chargeable_customers  # Return the actual customer list!
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/get-transactions', methods=['POST', 'OPTIONS'])
def get_transactions():
    """Get transaction statistics (Payments & Payouts)"""
    
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        print("📊 GET-TRANSACTIONS endpoint called!")
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        print("📊 Fetching payment intents...")
        
        # Get ALL Payment Intents (no limit)
        payment_intents = stripe.PaymentIntent.list(limit=100)
        
        all_transactions = 0
        succeeded = 0
        failed = 0
        refunded = 0
        disputed = 0
        payment_details = []
        
        # Get all payment intents
        for pi in payment_intents.auto_paging_iter():
            try:
                all_transactions += 1
                
                # Check status
                status = getattr(pi, 'status', 'unknown')
                if status == 'succeeded':
                    succeeded += 1
                elif status == 'canceled' or status == 'requires_payment_method':
                    failed += 1
                
                # Check for refunds (safely)
                amount_refunded = getattr(pi, 'amount_refunded', 0)
                if amount_refunded and amount_refunded > 0:
                    refunded += 1
                
                # Check for disputes (safely)
                is_disputed = getattr(pi, 'disputed', False)
                if is_disputed:
                    disputed += 1
                
                # Collect detailed payment information
                payment_method_details = getattr(pi, 'payment_method_details', None)
                payment_method_type = 'N/A'
                payment_method_brand = 'N/A'
                payment_method_last4 = 'N/A'
                
                if payment_method_details and hasattr(payment_method_details, 'card'):
                    payment_method_type = 'Card'
                    payment_method_brand = getattr(payment_method_details.card, 'brand', 'N/A').upper()
                    payment_method_last4 = getattr(payment_method_details.card, 'last4', 'N/A')
                
                # Get customer info (ID only - don't retrieve to avoid slowdown)
                customer_id = getattr(pi, 'customer', 'N/A')
                customer_display = customer_id if customer_id and customer_id != 'N/A' else 'N/A'
                
                # Get decline reason if failed
                decline_reason = 'N/A'
                if status in ['canceled', 'requires_payment_method', 'failed']:
                    last_payment_error = getattr(pi, 'last_payment_error', None)
                    if last_payment_error:
                        decline_reason = getattr(last_payment_error, 'message', 'Unknown error')
                
                # Get metadata for website and product name
                website = 'N/A'
                product_name = getattr(pi, 'description', 'N/A') or 'N/A'  # Default to description
                
                # Try to get website from metadata.site_url
                pi_metadata = getattr(pi, 'metadata', None)
                if pi_metadata:
                    if isinstance(pi_metadata, dict):
                        website = pi_metadata.get('site_url', 'N/A')
                    else:
                        website = getattr(pi_metadata, 'site_url', 'N/A')
                
                # If website still not found in PaymentIntent, try to get from Charge
                if website == 'N/A':
                    try:
                        charges = stripe.Charge.list(payment_intent=pi.id, limit=1)
                        if charges and len(charges.data) > 0:
                            charge = charges.data[0]
                            charge_metadata = getattr(charge, 'metadata', None)
                            if charge_metadata:
                                if isinstance(charge_metadata, dict):
                                    website = charge_metadata.get('site_url', 'N/A')
                                else:
                                    website = getattr(charge_metadata, 'site_url', 'N/A')
                    except Exception as charge_error:
                        print(f"⚠️ Could not retrieve charge metadata: {str(charge_error)}")
                
                # Check if payment has been refunded and update status
                display_status = status
                
                # Simple check: if amount_refunded exists but is still 0, refund might be pending
                # If amount_refunded > 0, refund is complete
                if amount_refunded and amount_refunded > 0:
                    if amount_refunded >= pi.amount:
                        display_status = 'refunded'  # Fully refunded
                    else:
                        display_status = 'partially_refunded'  # Partially refunded
                
                payment_details.append({
                    'id': pi.id,
                    'amount': pi.amount / 100,  # Convert from cents
                    'currency': getattr(pi, 'currency', 'usd').upper(),
                    'status': display_status,  # Use updated status that reflects refunds
                    'payment_method': f"{payment_method_brand} •••• {payment_method_last4}" if payment_method_last4 != 'N/A' else payment_method_type,
                    'description': getattr(pi, 'description', 'N/A') or 'No description',
                    'website': website,
                    'product_name': product_name,
                    'customer': customer_display,  # Show customer ID instead of email for speed
                    'date': datetime.fromtimestamp(pi.created).strftime('%Y-%m-%d %H:%M:%S'),
                    'decline_reason': decline_reason
                })
                
            except Exception as pi_error:
                print(f"⚠️ Error processing payment intent: {str(pi_error)}")
                continue
        
        # Get Payouts (get all)
        payout_details = []
        try:
            payouts = stripe.Payout.list(limit=100)
            
            total_payouts = 0
            paid_payouts = 0
            pending_payouts = 0
            failed_payouts = 0
            payout_amount = 0
            
            for payout in payouts.auto_paging_iter():
                try:
                    total_payouts += 1
                    
                    # Safely get amount
                    amount = getattr(payout, 'amount', 0)
                    payout_amount += amount / 100  # Convert from cents
                    
                    # Safely get status
                    status = getattr(payout, 'status', 'unknown')
                    if status == 'paid':
                        paid_payouts += 1
                    elif status == 'pending' or status == 'in_transit':
                        pending_payouts += 1
                    elif status == 'failed' or status == 'canceled':
                        failed_payouts += 1
                    
                    # Get bank destination info
                    destination_info = 'N/A'
                    destination_id = getattr(payout, 'destination', None)
                    if destination_id:
                        try:
                            # Try to retrieve bank account details
                            bank = stripe.BankAccount.retrieve(
                                destination_id,
                                stripe_account=getattr(payout, 'source_type', None)
                            ) if hasattr(stripe, 'BankAccount') else None
                            
                            if bank:
                                bank_name = getattr(bank, 'bank_name', 'N/A')
                                last4 = getattr(bank, 'last4', 'N/A')
                                destination_info = f"{bank_name} ••{last4}" if bank_name != 'N/A' else f"••{last4}"
                            else:
                                destination_info = destination_id[:20] + '...' if len(destination_id) > 20 else destination_id
                        except:
                            # If can't retrieve, just show the ID
                            destination_info = destination_id[:20] + '...' if len(destination_id) > 20 else destination_id
                    
                    # Collect payout details
                    payout_details.append({
                        'id': payout.id,
                        'amount': amount / 100,
                        'currency': getattr(payout, 'currency', 'usd').upper(),
                        'status': status,
                        'method': getattr(payout, 'method', 'standard'),
                        'type': getattr(payout, 'type', 'bank_account'),
                        'destination': destination_info,
                        'arrival_date': datetime.fromtimestamp(getattr(payout, 'arrival_date', payout.created)).strftime('%Y-%m-%d'),
                        'created': datetime.fromtimestamp(payout.created).strftime('%Y-%m-%d %H:%M:%S'),
                        'description': getattr(payout, 'description', 'N/A') or 'Payout'
                    })
                    
                except Exception as payout_error:
                    print(f"⚠️ Error processing payout: {str(payout_error)}")
                    continue
        except Exception as e:
            # Payouts might not be available for all accounts
            print(f"⚠️ Payouts not available: {str(e)}")
            total_payouts = 0
            paid_payouts = 0
            pending_payouts = 0
            failed_payouts = 0
            payout_amount = 0
        
        print(f"✅ Returning transaction stats: {all_transactions} payments, {total_payouts} payouts")
        
        return jsonify({
            'success': True,
            'payments': {
                'all': all_transactions,
                'succeeded': succeeded,
                'refunded': refunded,
                'disputed': disputed,
                'failed': failed,
                'details': payment_details  # Detailed payment list
            },
            'payouts': {
                'total': total_payouts,
                'paid': paid_payouts,
                'pending': pending_payouts,
                'failed': failed_payouts,
                'amount': round(payout_amount, 2),
                'details': payout_details  # Detailed payout list
            }
        })
    
    except Exception as e:
        print(f"❌ Error in get_transactions: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/get-overview', methods=['POST', 'OPTIONS'])
def get_overview():
    """Get account overview with date range filtering"""
    
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        date_range = data.get('dateRange', 'all_time')  # today, 7days, 4weeks, 6months, 12months, all_time
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        # Calculate date range
        from datetime import timedelta
        now = datetime.now()
        
        if date_range == 'today':
            start_date = int(datetime(now.year, now.month, now.day).timestamp())
        elif date_range == '7days':
            start_date = int((now - timedelta(days=7)).timestamp())
        elif date_range == '4weeks':
            start_date = int((now - timedelta(weeks=4)).timestamp())
        elif date_range == '6months':
            start_date = int((now - timedelta(days=180)).timestamp())
        elif date_range == '12months':
            start_date = int((now - timedelta(days=365)).timestamp())
        else:  # all_time
            start_date = 0
        
        # Get payment intents for date range
        print(f"📊 Fetching charges for date range: {date_range}, start_date: {start_date}")
        
        if start_date > 0:
            charges = stripe.Charge.list(limit=100, created={'gte': start_date})
        else:
            charges = stripe.Charge.list(limit=100)
        
        succeeded_amount = 0
        uncaptured_amount = 0
        refunded_amount = 0
        blocked_amount = 0
        failed_amount = 0
        dispute_count = 0
        total_charges = 0
        
        daily_gross = {}
        daily_net = {}
        
        # First pass to detect account currency from charges
        account_currency_from_charges = None
        for charge in charges.auto_paging_iter():
            charge_currency = getattr(charge, 'currency', 'usd').lower()
            if charge.status == 'succeeded':
                account_currency_from_charges = charge_currency
                break
        
        # Reset iterator
        if start_date > 0:
            charges = stripe.Charge.list(limit=100, created={'gte': start_date})
        else:
            charges = stripe.Charge.list(limit=100)
        
        # Use detected currency or default to usd
        detected_currency = account_currency_from_charges or 'usd'
        
        for charge in charges.auto_paging_iter():
            try:
                # Skip charges in different currencies
                charge_currency = getattr(charge, 'currency', 'usd').lower()
                if charge_currency != detected_currency:
                    continue
                
                total_charges += 1
                amount = charge.amount / 100
                created_date = datetime.fromtimestamp(charge.created).strftime('%Y-%m-%d')
                
                if charge.status == 'succeeded':
                    captured = getattr(charge, 'captured', True)
                    if captured:
                        succeeded_amount += amount
                        daily_gross[created_date] = daily_gross.get(created_date, 0) + amount
                        # Net = Gross - Refunds
                        refund_amount = getattr(charge, 'amount_refunded', 0) / 100
                        daily_net[created_date] = daily_net.get(created_date, 0) + (amount - refund_amount)
                    else:
                        uncaptured_amount += amount
                elif charge.status == 'failed':
                    failure_code = getattr(charge, 'failure_code', '')
                    if failure_code in ['card_declined', 'fraudulent', 'do_not_honor', 'blocked']:
                        blocked_amount += amount
                    else:
                        failed_amount += amount
                
                # Check refunds
                refunded_check = getattr(charge, 'refunded', False)
                amount_refunded = getattr(charge, 'amount_refunded', 0)
                if refunded_check or amount_refunded > 0:
                    refunded_amount += amount_refunded / 100
                
                # Check disputes
                is_disputed = getattr(charge, 'disputed', False)
                if is_disputed:
                    dispute_count += 1
                    
            except Exception as e:
                print(f"⚠️ Error processing charge: {str(e)}")
                continue
        
        print(f"📊 Account currency: {detected_currency.upper()}")
        
        print(f"📊 Processed {total_charges} charges, dispute_count: {dispute_count}")
        
        # Get balance (filter by detected currency)
        try:
            balance = stripe.Balance.retrieve()
            
            # Sum balance for the detected currency only
            available_balance = sum([bal['amount'] / 100 for bal in balance.available if bal.get('currency', 'usd').lower() == detected_currency])
            pending_balance = sum([bal['amount'] / 100 for bal in balance.pending if bal.get('currency', 'usd').lower() == detected_currency])
            
            print(f"💰 Balance ({detected_currency.upper()}): Available={available_balance}, Pending={pending_balance}")
        except Exception as e:
            print(f"⚠️ Balance error: {str(e)}")
            available_balance = 0
            pending_balance = 0
        
        # Get next payout (filter by detected currency)
        try:
            # Get all upcoming payouts (pending or in_transit)
            all_payouts = stripe.Payout.list(limit=10)
            next_payout = 0
            next_payout_date = 'N/A'
            
            for payout in all_payouts.data:
                payout_currency = getattr(payout, 'currency', 'usd').lower()
                # Only consider payouts in the same currency
                if payout_currency != detected_currency:
                    continue
                    
                status = getattr(payout, 'status', '')
                if status in ['pending', 'in_transit']:
                    next_payout = getattr(payout, 'amount', 0) / 100
                    arrival = getattr(payout, 'arrival_date', None)
                    if arrival:
                        next_payout_date = datetime.fromtimestamp(arrival).strftime('%Y-%m-%d')
                    break
            
            print(f"📅 Next payout ({detected_currency.upper()}): {next_payout} on {next_payout_date}")
        except Exception as e:
            print(f"⚠️ Next payout error: {str(e)}")
            next_payout = 0
            next_payout_date = 'N/A'
        
        # Prepare graph data
        sorted_dates = sorted(daily_gross.keys()) if daily_gross else []
        gross_data = [{'date': date, 'amount': round(daily_gross.get(date, 0), 2)} for date in sorted_dates]
        net_data = [{'date': date, 'amount': round(daily_net.get(date, 0), 2)} for date in sorted_dates]
        
        print(f"📈 Graph data: {len(gross_data)} days of data")
        
        # Calculate dispute rate
        dispute_rate = (dispute_count / max(1, total_charges)) * 100
        print(f"⚠️ Dispute rate: {dispute_rate}% ({dispute_count} disputes / {total_charges} charges)")
        
        result_data = {
            'success': True,
            'currency': detected_currency.upper(),
            'payments': {
                'succeeded': round(succeeded_amount, 2),
                'uncaptured': round(uncaptured_amount, 2),
                'refunded': round(refunded_amount, 2),
                'blocked': round(blocked_amount, 2),
                'failed': round(failed_amount, 2)
            },
            'graphs': {
                'gross_volume': gross_data,
                'net_volume': net_data,
                'dispute_rate': round(dispute_rate, 2)
            },
            'balance': {
                'available': round(available_balance, 2),
                'pending': round(pending_balance, 2)
            },
            'next_payout': {
                'amount': round(next_payout, 2) if next_payout else 0,
                'date': next_payout_date
            }
        }
        
        print(f"✅ Returning overview: {len(gross_data)} graph points, balance=${available_balance}, next payout=${next_payout}")
        return jsonify(result_data)
    
    except Exception as e:
        print(f"❌ Error in get_overview: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/charge', methods=['POST', 'OPTIONS'])
def charge_customers():
    """Charge customers - EXACT LOGIC from charge_all_customers.py"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        
        api_key = data.get('apiKey')
        amount_dollars = float(data.get('amount', 0))
        currency = data.get('currency', 'usd').lower()
        description = data.get('description', 'Subscription charge')
        max_customers = int(data.get('maxCustomers', 0))
        delay = float(data.get('delay', 1.0))
        provided_customers = data.get('customers', [])  # Accept pre-filtered customer list!
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        if amount_dollars <= 0:
            return jsonify({'success': False, 'error': 'Amount must be greater than 0'}), 400
        
        stripe.api_key = api_key
        amount_cents = int(amount_dollars * 100)
        
        # OPTIMIZATION: Use pre-filtered customers if provided (INSTANT!)
        if provided_customers and len(provided_customers) > 0:
            customers_to_charge = provided_customers
            print(f"⚡️ INSTANT: Using {len(provided_customers)} pre-filtered customers!")
        else:
            # Fallback: Filter customers on-the-fly (slower)
            print("⏱️ No customer list provided, filtering now...")
            customers = stripe.Customer.list(limit=100)
            customer_list = list(customers.auto_paging_iter())
            
            # Helper function to filter customers in parallel
            def check_customer_valid(customer):
                """Check if customer has valid payment method - PARALLEL"""
                try:
                    all_payment_methods = stripe.PaymentMethod.list(
                        customer=customer.id,
                        limit=10
                    )
                    
                    # Look for a valid card payment method (not Link/GPay/APay)
                    for pm in all_payment_methods.data:
                        # SKIP Link, Google Pay, Apple Pay
                        if pm.type in ['link', 'google_pay', 'apple_pay']:
                            continue
                        if hasattr(pm, 'link') and pm.link:
                            continue
                        
                        # Only accept regular card type
                        if pm.type == 'card':
                            # Make sure card is not wallet-connected
                            if hasattr(pm, 'card') and hasattr(pm.card, 'wallet'):
                                wallet_type = pm.card.wallet.get('type') if pm.card.wallet else None
                                if wallet_type in ['google_pay', 'apple_pay', 'link']:
                                    continue
                            
                            # Valid card found!
                            return {
                                'id': customer.id,
                                'email': customer.email or 'No email',
                                'name': customer.name or 'No name'
                            }
                    
                    # Fallback: Check for default source
                    if customer.default_source:
                        return {
                            'id': customer.id,
                            'email': customer.email or 'No email',
                            'name': customer.name or 'No name'
                        }
                    
                    # Fallback: Check invoice settings
                    if customer.invoice_settings and customer.invoice_settings.default_payment_method:
                        try:
                            pm = stripe.PaymentMethod.retrieve(customer.invoice_settings.default_payment_method)
                            if pm.type == 'card' and pm.type not in ['link', 'google_pay', 'apple_pay']:
                                if not (hasattr(pm, 'link') and pm.link):
                                    return {
                                        'id': customer.id,
                                        'email': customer.email or 'No email',
                                        'name': customer.name or 'No name'
                                    }
                        except:
                            pass
                    
                    return None
                except:
                    return None
            
            # Filter customers in PARALLEL (SUPER FAST!)
            customers_to_charge = []
            with ThreadPoolExecutor(max_workers=50) as executor:
                future_to_customer = {
                    executor.submit(check_customer_valid, customer): customer 
                    for customer in customer_list
                }
                
                for future in as_completed(future_to_customer):
                    result = future.result()
                    if result:
                        customers_to_charge.append(result)
        
        # Apply customer limit
        if max_customers > 0 and len(customers_to_charge) > max_customers:
            customers_to_charge = customers_to_charge[:max_customers]
        
        # Charge customers - PARALLEL PROCESSING for speed!
        results = {
            'success': True,
            'total': len(customers_to_charge),
            'successful': 0,
            'failed': 0,
            'charges': []
        }
        
        # Helper function for parallel charging
        def charge_single_customer(customer):
            """Charge a single customer - for parallel processing"""
            try:
                # Get customer's payment method - FORCE SKIP Link/GPay/APay
                cust_obj = stripe.Customer.retrieve(customer['id'])
                payment_method_id = None
                
                # Get all payment methods and find a valid card (not Link/GPay/APay)
                pms = stripe.PaymentMethod.list(customer=customer['id'], limit=10)
                
                for pm in pms.data:
                    # SKIP Link, Google Pay, Apple Pay
                    if pm.type in ['link', 'google_pay', 'apple_pay']:
                        continue
                    if hasattr(pm, 'link') and pm.link:
                        continue
                    
                    # Only use regular card payment methods
                    if pm.type == 'card':
                        # Check for wallet types too
                        if hasattr(pm, 'card') and hasattr(pm.card, 'wallet'):
                            wallet_type = pm.card.wallet.get('type') if pm.card.wallet else None
                            if wallet_type in ['google_pay', 'apple_pay', 'link']:
                                continue
                        
                        payment_method_id = pm.id
                        break
                
                # Try invoice settings if no valid card found
                if not payment_method_id and cust_obj.invoice_settings:
                    if cust_obj.invoice_settings.default_payment_method:
                        pm_id = cust_obj.invoice_settings.default_payment_method
                        try:
                            pm = stripe.PaymentMethod.retrieve(pm_id)
                            # Verify it's not Link/GPay/APay
                            if pm.type == 'card' and pm.type not in ['link', 'google_pay', 'apple_pay']:
                                if not (hasattr(pm, 'link') and pm.link):
                                    payment_method_id = pm_id
                        except:
                            pass
                
                if payment_method_id:
                    # Get payment method details before charging
                    pm_details = stripe.PaymentMethod.retrieve(payment_method_id)
                    
                    # Charge using the validated payment method
                    payment_intent = stripe.PaymentIntent.create(
                        amount=amount_cents,
                        currency=currency,
                        customer=customer['id'],
                        payment_method=payment_method_id,
                        description=description,
                        confirm=True,
                        off_session=True,
                        payment_method_types=['card'],
                    )
                    charge_id = payment_intent.id
                    
                    # Get card details
                    card_info = {
                        'brand': pm_details.card.brand if hasattr(pm_details, 'card') else 'Unknown',
                        'last4': pm_details.card.last4 if hasattr(pm_details, 'card') else '****',
                        'exp_month': pm_details.card.exp_month if hasattr(pm_details, 'card') else '',
                        'exp_year': pm_details.card.exp_year if hasattr(pm_details, 'card') else '',
                    }
                else:
                    # Fallback: Try to charge with default source (older Sources API)
                    charge = stripe.Charge.create(
                        amount=amount_cents,
                        currency=currency,
                        customer=customer['id'],
                        description=description,
                    )
                    charge_id = charge.id
                    
                    # Try to get card details from charge
                    card_info = {
                        'brand': charge.payment_method_details.card.brand if hasattr(charge, 'payment_method_details') else 'Unknown',
                        'last4': charge.payment_method_details.card.last4 if hasattr(charge, 'payment_method_details') else '****',
                        'exp_month': '',
                        'exp_year': '',
                    }
                
                # Add small delay to avoid rate limits
                if delay > 0:
                    time.sleep(delay)
                
                return {
                    'status': 'success',
                    'customer': customer,
                    'chargeId': charge_id,
                    'amount': amount_dollars,
                    'currency': currency.upper(),
                    'timestamp': datetime.now().isoformat(),
                    'card': card_info,
                    'description': description
                }
            
            except Exception as e:
                error_msg = getattr(e, 'user_message', str(e))
                error_code = getattr(e, 'code', 'unknown')
                error_type = type(e).__name__
                
                return {
                    'status': 'failed',
                    'customer': customer,
                    'error': error_msg,
                    'errorCode': error_code,
                    'errorType': error_type,
                    'timestamp': datetime.now().isoformat()
                }
        
        # Use parallel processing to charge customers FAST!
        # Process up to 10 customers at once (safe for Stripe Radar)
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all charging tasks
            future_to_customer = {
                executor.submit(charge_single_customer, customer): customer 
                for customer in customers_to_charge
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_customer):
                result = future.result()
                
                if result['status'] == 'success':
                    results['successful'] += 1
                    results['charges'].append(result)
                else:
                    results['failed'] += 1
                    results['charges'].append(result)
        
        return jsonify(results)
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/refund', methods=['POST', 'OPTIONS'])
def refund_payment():
    """Refund a payment"""
    
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        payment_intent_id = data.get('paymentIntentId')
        refund_amount = data.get('amount')  # Optional: partial refund
        reason = data.get('reason', 'requested_by_customer')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        if not payment_intent_id:
            return jsonify({'success': False, 'error': 'Payment Intent ID is required'}), 400
        
        stripe.api_key = api_key
        
        print(f"💸 Refund request for: {payment_intent_id}")
        
        # Get the payment intent to find the charge
        payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        
        # Get the charge ID from the payment intent
        charge_id = None
        if hasattr(payment_intent, 'latest_charge'):
            charge_id = payment_intent.latest_charge
        elif hasattr(payment_intent, 'charges') and payment_intent.charges.data:
            charge_id = payment_intent.charges.data[0].id
        
        if not charge_id:
            return jsonify({'success': False, 'error': 'No charge found for this payment'}), 400
        
        # Create refund
        refund_params = {
            'charge': charge_id,
            'reason': reason
        }
        
        # Add amount if partial refund
        if refund_amount:
            refund_params['amount'] = int(refund_amount * 100)  # Convert to cents
        
        refund = stripe.Refund.create(**refund_params)
        
        print(f"✅ Refund created: {refund.id}")
        
        return jsonify({
            'success': True,
            'refund': {
                'id': refund.id,
                'amount': refund.amount / 100,
                'currency': refund.currency.upper(),
                'status': refund.status,
                'reason': refund.reason
            }
        })
    
    except Exception as e:
        print(f"❌ Refund error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/get-connected-accounts', methods=['POST', 'OPTIONS'])
def get_connected_accounts():
    """Get all connected Stripe accounts"""
    
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json()
        api_key = data.get('apiKey')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        stripe.api_key = api_key
        
        print("🔗 Fetching connected accounts...")
        
        # Retrieve all connected accounts
        accounts_list = stripe.Account.list(limit=100)
        
        accounts = []
        
        if hasattr(accounts_list, 'data'):
            for account in accounts_list.data:
                account_data = {
                    'id': account.get('id', 'N/A'),
                    'email': account.get('email', 'N/A'),
                    'country': account.get('country', 'N/A'),
                    'type': account.get('type', 'N/A'),
                    'charges_enabled': account.get('charges_enabled', False),
                    'payouts_enabled': account.get('payouts_enabled', False),
                    'created': account.get('created', 0),
                    'business_profile': {
                        'name': account.get('business_profile', {}).get('name', 'N/A'),
                        'url': account.get('business_profile', {}).get('url', 'N/A'),
                    },
                    'requirements': {
                        'past_due': account.get('requirements', {}).get('past_due', []),
                        'currently_due': account.get('requirements', {}).get('currently_due', []),
                        'pending_verification': account.get('requirements', {}).get('pending_verification', []),
                        'eventually_due': account.get('requirements', {}).get('eventually_due', [])
                    }
                }
                accounts.append(account_data)
        
        print(f"✅ Found {len(accounts)} connected accounts")
        
        return jsonify({
            'success': True,
            'accounts': accounts,
            'total': len(accounts)
        })
    
    except Exception as e:
        print(f"❌ Error fetching connected accounts: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'accounts': [],
            'total': 0
        }), 400


# ============================================================
# START SERVER
# ============================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print("🚀 Stripe Rebilling Backend Server - ULTRA FAST MODE")
    print("="*70)
    print("\n✅ CORS enabled for all origins")
    print("✅ Using WORKING logic from charge_all_customers.py")
    print("✅ Customer diagnostic endpoint added")
    print("⚡️ PARALLEL PROCESSING: 100 workers for customer loading")
    print("⚡️ PARALLEL CHARGING: 10 customers charged simultaneously")
    print("🚫 FORCE SKIP: Link, Google Pay, Apple Pay (automatic)")
    print("🌐 Server running at: http://localhost:5001")
    print("\n📝 Next steps:")
    print("   1. Open another terminal")
    print("   2. cd frontend")
    print("   3. python3 -m http.server 8000")
    print("   4. Open browser: http://localhost:8000")
    print("\n💡 Payment Filtering:")
    print("   • Link payment method - SKIPPED")
    print("   • Google Pay - SKIPPED")
    print("   • Apple Pay - SKIPPED")
    print("   • Regular cards - CHARGED")
    print("\n" + "="*70 + "\n")
    
    app.run(host='0.0.0.0', port=5001, debug=True)
