import 'package:flutter/material.dart';
import '../../core/theme.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _obscure = true;

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
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(20),
                  child: Column(
                    children: [
                      TextField(controller: _email, decoration: const InputDecoration(labelText: 'Email'), keyboardType: TextInputType.emailAddress),
                      const SizedBox(height: 12),
                      TextField(
                        controller: _password,
                        decoration: InputDecoration(
                          labelText: 'Password',
                          suffixIcon: IconButton(
                            icon: Icon(_obscure ? Icons.visibility : Icons.visibility_off),
                            onPressed: () => setState(() => _obscure = !_obscure),
                          ),
                        ),
                        obscureText: _obscure,
                      ),
                      const SizedBox(height: 8),
                      Row(children: [Checkbox(value: true, onChanged: (_) {}), const Text('Remember this device')]),
                      const SizedBox(height: 16),
                      SizedBox(
                        width: double.infinity,
                        child: ElevatedButton(
                          onPressed: _email.text.isNotEmpty && _password.text.isNotEmpty
                              ? () => Navigator.pushReplacementNamed(context, '/setup')
                              : null,
                          child: const Text('Sign In'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const Spacer(),
              Text('NeoGuard AI — For authorised clinical personnel only',
                  style: TextStyle(color: Colors.grey.shade600, fontSize: 11), textAlign: TextAlign.center),
            ],
          ),
        ),
      ),
    );
  }
}
