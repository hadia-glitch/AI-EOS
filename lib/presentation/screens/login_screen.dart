import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../../core/theme.dart';
import '../../data/auth_service.dart';
import '../../data/supabase_config.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _formKey = GlobalKey<FormState>();
  bool _obscure = true;
  bool _isSignup = false;
  bool _loading = false;
  bool _emailNotConfirmed = false;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (!SupabaseConfig.isConfigured) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Supabase is not configured. Start Flutter with SUPABASE_URL and SUPABASE_ANON_KEY dart defines.')),
      );
      return;
    }

    setState(() {
      _loading = true;
      _emailNotConfirmed = false;
    });

    try {
      if (_isSignup) {
        final response = await AuthService.instance.signUp(
          email: _email.text.trim(),
          password: _password.text,
        );
        if (!mounted) return;
        // If email confirmation is disabled, user is signed in immediately → go to splash
        if (response.user != null && response.session != null) {
          context.go('/');
          return;
        }
        // Email confirmation is enabled → stay on login, show instructions
        setState(() => _isSignup = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Account created! Check your email to confirm, then sign in.'),
            duration: Duration(seconds: 6),
          ),
        );
        return;
      }

      await AuthService.instance.signIn(
        email: _email.text.trim(),
        password: _password.text,
      );

      if (!mounted) return;
      if (!mounted) return;
      context.go('/');
    } catch (e) {
      if (!mounted) return;
      final message = e.toString();

      if (message.contains('email_not_confirmed') || message.contains('Email not confirmed')) {
        setState(() => _emailNotConfirmed = true);
      } else if (message.contains('over_email_send_rate_limit') || message.contains('rate_limit')) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Too many requests. Please wait a moment before trying again.'),
            duration: Duration(seconds: 5),
          ),
        );
      } else if (message.contains('Invalid login credentials')) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Incorrect email or password.')),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Authentication failed: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _resendConfirmation() async {
    if (_email.text.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Enter your email address first.')),
      );
      return;
    }
    setState(() => _loading = true);
    try {
      await AuthService.instance.resendConfirmation(email: _email.text.trim());
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Confirmation email resent. Check your inbox.')),
      );
    } catch (e) {
      if (!mounted) return;
      final message = e.toString();
      if (message.contains('rate_limit') || message.contains('over_email_send_rate_limit')) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Please wait before requesting another email.')),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not resend: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            children: [
              const SizedBox(height: 40),
              const Icon(Icons.security, size: 48, color: WhoTheme.primaryNavy),
              const SizedBox(height: 12),
              Text('NeoGuard AI', style: Theme.of(context).textTheme.headlineMedium),
              const Text('Clinical Login', style: TextStyle(color: WhoTheme.secondaryTeal, fontSize: 16)),
              const SizedBox(height: 32),
              Form(
                key: _formKey,
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      children: [
                        Text(
                          _isSignup ? 'Create secure account' : 'Sign in to your account',
                          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
                        ),
                        const SizedBox(height: 16),
                        TextFormField(
                          controller: _email,
                          decoration: const InputDecoration(labelText: 'Email'),
                          keyboardType: TextInputType.emailAddress,
                          onChanged: (_) {
                            if (_emailNotConfirmed) setState(() => _emailNotConfirmed = false);
                          },
                          validator: (value) {
                            if (value == null || !value.contains('@')) return 'Enter a valid email';
                            return null;
                          },
                        ),
                        const SizedBox(height: 12),
                        TextFormField(
                          controller: _password,
                          decoration: InputDecoration(
                            labelText: 'Password',
                            suffixIcon: IconButton(
                              icon: Icon(_obscure ? Icons.visibility : Icons.visibility_off),
                              onPressed: () => setState(() => _obscure = !_obscure),
                            ),
                          ),
                          obscureText: _obscure,
                          validator: (value) {
                            if (value == null || value.length < 6) return 'Use at least 6 characters';
                            return null;
                          },
                        ),
                        if (_emailNotConfirmed) ...[
                          const SizedBox(height: 12),
                          Container(
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: Colors.amber.shade50,
                              border: Border.all(color: Colors.amber.shade300),
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Row(
                                  children: [
                                    Icon(Icons.mail_outline, color: Colors.orange, size: 18),
                                    SizedBox(width: 8),
                                    Text(
                                      'Email not confirmed',
                                      style: TextStyle(fontWeight: FontWeight.bold, color: Colors.orange),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 4),
                                const Text(
                                  'Check your inbox and click the confirmation link. For testing, disable email confirmation in Supabase Dashboard → Auth → Settings.',
                                  style: TextStyle(fontSize: 12, color: Colors.black87),
                                ),
                                const SizedBox(height: 8),
                                TextButton.icon(
                                  onPressed: _loading ? null : _resendConfirmation,
                                  icon: const Icon(Icons.refresh, size: 16),
                                  label: const Text('Resend confirmation email'),
                                  style: TextButton.styleFrom(
                                    padding: EdgeInsets.zero,
                                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                        const SizedBox(height: 8),

                        const SizedBox(height: 16),
                        SizedBox(
                          width: double.infinity,
                          child: ElevatedButton(
                            onPressed: _loading ? null : _submit,
                            child: _loading
                                ? const SizedBox(
                                    height: 18,
                                    width: 18,
                                    child: CircularProgressIndicator(strokeWidth: 2),
                                  )
                                : Text(_isSignup ? 'Sign Up' : 'Sign In'),
                          ),
                        ),
                        TextButton(
                          onPressed: _loading
                              ? null
                              : () => setState(() {
                                    _isSignup = !_isSignup;
                                    _emailNotConfirmed = false;
                                  }),
                          child: Text(_isSignup ? 'Already have an account? Sign in' : 'Need an account? Sign up'),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              const Spacer(),
              Text('NeoGuard AI - For authorised clinical personnel only',
                  style: TextStyle(color: Colors.grey.shade600, fontSize: 11), textAlign: TextAlign.center),
            ],
          ),
        ),
      ),
    );
  }
}