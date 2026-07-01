from django import forms
from django.contrib.auth.models import User
import json
import requests
from urllib.parse import urlparse
from .models import Environment, Project, TestCase, UserProfile, ThemeSettings


class LoginForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'Username'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control', 'placeholder': 'Password'
    }))


class EnvironmentForm(forms.ModelForm):
    base_url = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'https://api.example.com'
        })
    )

    oauth2_add_to = forms.ChoiceField(
        required=False,
        choices=[
            ('request_headers', 'Request Headers'),
            ('request_url', 'Request URL'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    basic_username = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    basic_password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'render_value': True}))
    digest_username = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    digest_password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'render_value': True}))
    bearer_token = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}))
    bearer_add_to = forms.ChoiceField(
        required=False,
        choices=[
            ('request_headers', 'Request Headers'),
            ('request_url', 'Request URL (Query Param)'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    bearer_header_prefix = forms.CharField(required=False, initial='Bearer', widget=forms.TextInput(attrs={'class': 'form-control'}))
    bearer_query_param_name = forms.CharField(required=False, initial='access_token', widget=forms.TextInput(attrs={'class': 'form-control'}))
    api_key_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    api_key_value = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    api_key_add_to = forms.ChoiceField(
        required=False,
        choices=[
            ('request_headers', 'Request Headers'),
            ('request_url', 'Request URL (Query Param)'),
            ('cookie', 'Cookie Header'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    oauth1_add_to = forms.ChoiceField(
        required=False,
        choices=[
            ('request_headers', 'Request Headers'),
            ('request_url', 'Request URL (Query Param)'),
            ('request_body', 'Request Body/Form Data'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    oauth1_consumer_key = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    oauth1_consumer_secret = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    oauth1_token = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    oauth1_token_secret = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    oauth1_signature_method = forms.ChoiceField(
        required=False,
        choices=[
            ('HMAC-SHA1', 'HMAC-SHA1'),
            ('HMAC-SHA256', 'HMAC-SHA256'),
            ('RSA-SHA1', 'RSA-SHA1'),
            ('PLAINTEXT', 'PLAINTEXT'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    awsv4_access_key = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    awsv4_secret_key = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    awsv4_region = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'us-east-1'}))
    awsv4_service = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'execute-api'}))
    awsv4_session_token = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    awsv4_add_to = forms.ChoiceField(
        required=False,
        choices=[
            ('request_headers', 'Request Headers'),
            ('request_url', 'Request URL (Presigned style)'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    ntlm_username = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    ntlm_password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'render_value': True}))
    oauth2_client_id = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    oauth2_client_secret = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    oauth2_token_url = forms.CharField(required=False, widget=forms.URLInput(attrs={'class': 'form-control'}))
    oauth2_header_prefix = forms.CharField(required=False, initial='Bearer', widget=forms.TextInput(attrs={'class': 'form-control'}))
    oauth2_current_token = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'readonly': 'readonly'}))

    class Meta:
        model = Environment
        fields = ['name', 'base_url', 'description', 'auth_type', 'auth_credentials', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'base_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://api.example.com'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'auth_type': forms.Select(attrs={'class': 'form-select'}),
            'auth_credentials': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 4,
                'placeholder': '{"username": "user", "password": "pass"}'
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        creds = {}
        if self.instance and self.instance.pk and self.instance.auth_credentials:
            try:
                creds = json.loads(self.instance.auth_credentials)
            except json.JSONDecodeError:
                creds = {}

        self.fields['oauth2_add_to'].initial = creds.get('add_to', 'request_headers')
        self.fields['oauth2_client_id'].initial = creds.get('client_id', '')
        self.fields['oauth2_client_secret'].initial = creds.get('client_secret', '')
        self.fields['oauth2_token_url'].initial = creds.get('token_url', '')
        self.fields['oauth2_header_prefix'].initial = creds.get('header_prefix', 'Bearer')
        self.fields['oauth2_current_token'].initial = creds.get('token', '')

        self.fields['basic_username'].initial = creds.get('username', '')
        self.fields['basic_password'].initial = creds.get('password', '')
        self.fields['digest_username'].initial = creds.get('username', '')
        self.fields['digest_password'].initial = creds.get('password', '')
        self.fields['bearer_token'].initial = creds.get('token', '')
        self.fields['bearer_add_to'].initial = creds.get('add_to', 'request_headers')
        self.fields['bearer_header_prefix'].initial = creds.get('header_prefix', 'Bearer')
        self.fields['bearer_query_param_name'].initial = creds.get('query_param_name', 'access_token')

        self.fields['api_key_name'].initial = creds.get('key_name', creds.get('key', 'X-API-Key'))
        self.fields['api_key_value'].initial = creds.get('key_value', creds.get('value', ''))
        self.fields['api_key_add_to'].initial = creds.get('add_to', 'request_headers')

        self.fields['oauth1_add_to'].initial = creds.get('add_to', 'request_headers')
        self.fields['oauth1_consumer_key'].initial = creds.get('consumer_key', creds.get('client_key', ''))
        self.fields['oauth1_consumer_secret'].initial = creds.get('consumer_secret', creds.get('client_secret', ''))
        self.fields['oauth1_token'].initial = creds.get('token', creds.get('resource_owner_key', ''))
        self.fields['oauth1_token_secret'].initial = creds.get('token_secret', creds.get('resource_owner_secret', ''))
        self.fields['oauth1_signature_method'].initial = creds.get('signature_method', 'HMAC-SHA1')

        self.fields['awsv4_access_key'].initial = creds.get('access_key', '')
        self.fields['awsv4_secret_key'].initial = creds.get('secret_key', '')
        self.fields['awsv4_region'].initial = creds.get('region', 'us-east-1')
        self.fields['awsv4_service'].initial = creds.get('service', 'execute-api')
        self.fields['awsv4_session_token'].initial = creds.get('session_token', '')
        self.fields['awsv4_add_to'].initial = creds.get('add_to', 'request_headers')

        self.fields['ntlm_username'].initial = creds.get('username', '')
        self.fields['ntlm_password'].initial = creds.get('password', '')

    def clean(self):
        cleaned_data = super().clean()
        auth_type = cleaned_data.get('auth_type') or 'none'

        if auth_type == 'none':
            cleaned_data['auth_credentials'] = ''
            return cleaned_data

        if auth_type == 'basic':
            username = (cleaned_data.get('basic_username') or '').strip()
            password = cleaned_data.get('basic_password') or ''
            if not username or not password:
                raise forms.ValidationError('Basic Auth requires username and password.')
            cleaned_data['auth_credentials'] = json.dumps({'username': username, 'password': password})
            return cleaned_data

        if auth_type == 'digest':
            username = (cleaned_data.get('digest_username') or '').strip()
            password = cleaned_data.get('digest_password') or ''
            if not username or not password:
                raise forms.ValidationError('Digest Auth requires username and password.')
            cleaned_data['auth_credentials'] = json.dumps({'username': username, 'password': password})
            return cleaned_data

        if auth_type == 'bearer':
            token = (cleaned_data.get('bearer_token') or '').strip()
            add_to = (cleaned_data.get('bearer_add_to') or 'request_headers').strip() or 'request_headers'
            header_prefix = (cleaned_data.get('bearer_header_prefix') or 'Bearer').strip() or 'Bearer'
            query_param_name = (cleaned_data.get('bearer_query_param_name') or 'access_token').strip() or 'access_token'
            if not token:
                raise forms.ValidationError('Bearer Token auth requires token.')
            cleaned_data['auth_credentials'] = json.dumps({
                'token': token,
                'add_to': add_to,
                'header_prefix': header_prefix,
                'query_param_name': query_param_name,
            })
            return cleaned_data

        if auth_type == 'api_key':
            key_name = (cleaned_data.get('api_key_name') or '').strip()
            key_value = (cleaned_data.get('api_key_value') or '').strip()
            add_to = (cleaned_data.get('api_key_add_to') or 'request_headers').strip() or 'request_headers'
            if not key_name or not key_value:
                raise forms.ValidationError('API Key auth requires key name and key value.')
            cleaned_data['auth_credentials'] = json.dumps({
                'key_name': key_name,
                'key_value': key_value,
                'add_to': add_to,
            })
            return cleaned_data

        if auth_type == 'oauth1':
            consumer_key = (cleaned_data.get('oauth1_consumer_key') or '').strip()
            consumer_secret = (cleaned_data.get('oauth1_consumer_secret') or '').strip()
            token = (cleaned_data.get('oauth1_token') or '').strip()
            token_secret = (cleaned_data.get('oauth1_token_secret') or '').strip()
            add_to = (cleaned_data.get('oauth1_add_to') or 'request_headers').strip() or 'request_headers'
            signature_method = (cleaned_data.get('oauth1_signature_method') or 'HMAC-SHA1').strip() or 'HMAC-SHA1'

            if not consumer_key or not consumer_secret:
                raise forms.ValidationError('OAuth 1.0 requires consumer key and consumer secret.')

            cleaned_data['auth_credentials'] = json.dumps({
                'consumer_key': consumer_key,
                'consumer_secret': consumer_secret,
                'token': token,
                'token_secret': token_secret,
                'add_to': add_to,
                'signature_method': signature_method,
            })
            return cleaned_data

        if auth_type == 'awsv4':
            access_key = (cleaned_data.get('awsv4_access_key') or '').strip()
            secret_key = (cleaned_data.get('awsv4_secret_key') or '').strip()
            region = (cleaned_data.get('awsv4_region') or '').strip()
            service = (cleaned_data.get('awsv4_service') or '').strip()
            session_token = (cleaned_data.get('awsv4_session_token') or '').strip()
            add_to = (cleaned_data.get('awsv4_add_to') or 'request_headers').strip() or 'request_headers'

            if not access_key or not secret_key or not region or not service:
                raise forms.ValidationError('AWS Signature requires access key, secret key, region, and service.')

            cleaned_data['auth_credentials'] = json.dumps({
                'access_key': access_key,
                'secret_key': secret_key,
                'region': region,
                'service': service,
                'session_token': session_token,
                'add_to': add_to,
            })
            return cleaned_data

        if auth_type == 'ntlm':
            username = (cleaned_data.get('ntlm_username') or '').strip()
            password = cleaned_data.get('ntlm_password') or ''
            if not username or not password:
                raise forms.ValidationError('NTLM auth requires username and password.')
            cleaned_data['auth_credentials'] = json.dumps({'username': username, 'password': password})
            return cleaned_data

        if auth_type == 'oauth2':
            add_to = cleaned_data.get('oauth2_add_to') or 'request_headers'
            client_id = (cleaned_data.get('oauth2_client_id') or '').strip()
            client_secret = (cleaned_data.get('oauth2_client_secret') or '').strip()
            token_url = (cleaned_data.get('oauth2_token_url') or '').strip()
            header_prefix = (cleaned_data.get('oauth2_header_prefix') or 'Bearer').strip() or 'Bearer'

            if not client_id or not client_secret or not token_url:
                raise forms.ValidationError('OAuth 2.0 requires Client ID, Client Secret, and Access Token URL.')

            try:
                response = requests.post(
                    token_url,
                    data={
                        'grant_type': 'client_credentials',
                        'client_id': client_id,
                        'client_secret': client_secret,
                    },
                    timeout=20,
                    verify=True,
                )
            except requests.exceptions.RequestException as e:
                raise forms.ValidationError(f'Unable to fetch OAuth token: {str(e)}')

            if not response.ok:
                raise forms.ValidationError(f'OAuth token endpoint returned {response.status_code}.')

            try:
                payload = response.json()
            except ValueError:
                raise forms.ValidationError('OAuth token endpoint returned non-JSON response.')

            token = payload.get('access_token')
            if not token:
                raise forms.ValidationError('OAuth token endpoint did not return access_token.')

            cleaned_data['oauth2_current_token'] = token
            cleaned_data['auth_credentials'] = json.dumps({
                'add_to': add_to,
                'client_id': client_id,
                'client_secret': client_secret,
                'token_url': token_url,
                'header_prefix': header_prefix,
                'token': token,
            })
            return cleaned_data

        cleaned_data['auth_credentials'] = cleaned_data.get('auth_credentials') or ''
        return cleaned_data

    def clean_base_url(self):
        value = (self.cleaned_data.get('base_url') or '').strip()
        parsed = urlparse(value)

        if parsed.scheme not in ('http', 'https'):
            raise forms.ValidationError('Enter a valid URL starting with http:// or https://')
        if not parsed.netloc:
            raise forms.ValidationError('Enter a valid URL with a host name.')
        if ' ' in value:
            raise forms.ValidationError('URL cannot contain spaces.')

        return value


class TestCaseForm(forms.ModelForm):
    project = forms.ChoiceField(
        required=False,
        choices=[('', '-- Select Project Folder --')],
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    expected_status_code = forms.TypedChoiceField(
        required=False,
        choices=[
            ('', '-- Select Status Code --'),
            ('200', '200'),
            ('201', '201'),
            ('400', '400'),
            ('401', '401'),
            ('404', '404'),
            ('500', '500'),
        ],
        coerce=int,
        empty_value=None,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        project_names = list(Project.objects.values_list('name', flat=True).order_by('name'))
        legacy_project_names = list(TestCase.objects.exclude(project='').values_list('project', flat=True).distinct().order_by('project'))

        combined = sorted(set(project_names + legacy_project_names))
        choices = [('', '-- Select Project Folder --')] + [(name, name) for name in combined]
        self.fields['project'].choices = choices

        if self.instance and self.instance.pk and self.instance.project and self.instance.project not in dict(choices):
            self.fields['project'].choices.append((self.instance.project, self.instance.project))

    class Meta:
        model = TestCase
        fields = [
            'name', 'description', 'module', 'project', 'endpoint',
            'http_method', 'headers', 'query_params', 'path_params',
            'request_body', 'form_data', 'auth_type', 'auth_credentials',
            'expected_status_code', 'expected_response_content',
            'expected_response_time_ms', 'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'module': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Authentication'}),
            'project': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., MyProject'}),
            'endpoint': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '/api/v1/resource'}),
            'http_method': forms.Select(attrs={'class': 'form-select'}),
            'headers': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 4,
                'placeholder': '{"Content-Type": "application/json"}'
            }),
            'query_params': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 3,
                'placeholder': '{"page": "1", "limit": "10"}'
            }),
            'path_params': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 3,
                'placeholder': '{"id": "123"}'
            }),
            'request_body': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 15,
                'placeholder': '{"key": "value"}',
                'style': 'font-family: monospace; font-size: 0.85rem; resize: vertical;'
            }),
            'form_data': forms.HiddenInput(),
            'auth_type': forms.Select(attrs={'class': 'form-select'}),
            'auth_credentials': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 3,
                'placeholder': '{"token": "your-token-here"}'
            }),
            'expected_status_code': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '200'}),
            'expected_response_content': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 4,
                'placeholder': 'Expected content in response'
            }),
            'expected_response_time_ms': forms.NumberInput(attrs={
                'class': 'form-control', 'placeholder': '2000'
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class UserCreateForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    first_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        if password and confirm_password and password != confirm_password:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already exists.")
        return username


class ProjectCreateForm(forms.Form):
    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Export LC Project'})
    )

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if Project.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError('Project folder already exists.')
        return name


class UserEditForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control'}))
    first_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))
    is_active = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    new_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to keep current'})
    )
    confirm_new_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm new password'})
    )

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_new_password = cleaned_data.get('confirm_new_password')
        if new_password and new_password != confirm_new_password:
            raise forms.ValidationError("New passwords do not match.")
        return cleaned_data


class ThemeSettingsForm(forms.ModelForm):
    class Meta:
        model = ThemeSettings
        fields = [
            'theme_mode',
            'primary_color',
            'accent_color',
            'background_color',
            'surface_color',
            'sidebar_start_color',
            'sidebar_end_color',
            'text_color',
            'border_color',
        ]
        widgets = {
            'theme_mode': forms.Select(attrs={'class': 'form-select'}),
            'primary_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'accent_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'background_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'surface_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'sidebar_start_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'sidebar_end_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'text_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
            'border_color': forms.TextInput(attrs={'class': 'form-control', 'type': 'color'}),
        }
